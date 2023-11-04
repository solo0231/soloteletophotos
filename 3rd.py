import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    CallbackContext,
    ConversationHandler,
)
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
import requests

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)
logger = logging.getLogger(__name__)

# Google Photos API information
SCOPES = ['https://www.googleapis.com/auth/photoslibrary.appendonly']
GOOGLE_PHOTOS_UPLOAD_URL = 'https://photoslibrary.googleapis.com/v1/uploads'
GOOGLE_PHOTOS_BATCH_CREATE_URL = 'https://photoslibrary.googleapis.com/v1/mediaItems:batchCreate'

# Telegram Bot Token from BotFather
TELEGRAM_TOKEN = '6286244362:AAFAUUDDOQUV4FWaBR7AYd9HLBLusGhldvI'

# The folder where received photos will be stored
PHOTOS_FOLDER = 'photos'
if not os.path.exists(PHOTOS_FOLDER):
    os.makedirs(PHOTOS_FOLDER)

# Conversation states
PHOTO_UPLOAD, UPLOAD_CONFIRMATION = range(2)

def authenticate_google_photos():
    creds = None
    # Check if token.json exists, which stores user's access and refresh tokens
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        # Save the credentials for the next run
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    return creds

def upload_file(filepath, token):
    headers = {
        'Authorization': f"Bearer {token}",
        'Content-type': 'application/octet-stream',
        'X-Goog-Upload-File-Name': os.path.basename(filepath),
        'X-Goog-Upload-Protocol': 'raw',
    }
    img = open(filepath, 'rb').read()
    response = requests.post(GOOGLE_PHOTOS_UPLOAD_URL, data=img, headers=headers)
    return response.content.decode()

def create_media_item(upload_token, token):
    headers = {
        'Authorization': f"Bearer {token}",
        'Content-type': 'application/json',
    }
    body = {
        'newMediaItems': [
            {
                'simpleMediaItem': {
                    'uploadToken': upload_token
                }
            }
        ]
    }
    response = requests.post(GOOGLE_PHOTOS_BATCH_CREATE_URL, json=body, headers=headers)
    return response.json()

def start(update: Update, context: CallbackContext) -> int:
    update.message.reply_text(
        'Hi! Send me the photos and videos you want to upload to Google Photos. '
        'Send /done when you are finished sending photos and videos.'
    )
    context.user_data['photos'] = []  # Initialize the photos list
    return PHOTO_UPLOAD

def photo_video_or_document(update: Update, context: CallbackContext) -> int:
    # Initialize file_path and file_id
    file_path = None
    file_id = None

    # Check if it's a photo
    if update.message.photo:
        file = update.message.photo[-1].get_file()
        file_path = os.path.join(PHOTOS_FOLDER, f"{file.file_id}.jpg")
        file_id = file.file_id
        file.download(file_path)
    # Check if it's a video
    elif update.message.video:
        file = update.message.video.get_file()
        file_path = os.path.join(PHOTOS_FOLDER, f"{file.file_id}.mp4")
        file_id = file.file_id
        file.download(file_path)
    # If it's a document, check if it's an image or video by mime type
    elif update.message.document:
        if 'image' in update.message.document.mime_type:
            file = update.message.document.get_file()
            file_path = os.path.join(PHOTOS_FOLDER, f"{file.file_id}.jpg")
            file_id = file.file_id
            file.download(file_path)
        elif 'video' in update.message.document.mime_type:
            file = update.message.document.get_file()
            file_path = os.path.join(PHOTOS_FOLDER, f"{file.file_id}.mp4")
            file_id = file.file_id
            file.download(file_path)

    # If a file was successfully retrieved and saved
    if file_path and file_id:
        context.user_data.setdefault('files', []).append(file_path)
        return PHOTO_UPLOAD
    else:
        update.message.reply_text("Please send a photo, a video, or an image/video file.")
        return PHOTO_UPLOAD

def done(update: Update, context: CallbackContext) -> int:
    files = context.user_data.get('files', [])
    if not files:
        update.message.reply_text('No files to upload. Send /start to begin.')
        return ConversationHandler.END

    # Send a single message summarizing the received files
    message = update.message.reply_text(f'Received {len(files)} files. Starting upload process now.')

    # Authenticate Google Photos API once for all uploads
    creds = authenticate_google_photos()

    # Upload all files with progress
    successful_uploads = 0
    for idx, filepath in enumerate(files, start=1):
        try:
            upload_token = upload_file(filepath, creds.token)
            create_media_item(upload_token, creds.token)
            successful_uploads += 1
            message.edit_text(f'Uploaded {idx}/{len(files)} files.')
        except Exception as e:
            logger.error(f"Failed to upload {filepath}: {e}")
            message.edit_text(f'Failed to upload file {idx}. Error: {e}')
            break
        finally:
            os.remove(filepath)  # Delete the file after upload

    if successful_uploads == len(files):
        # After uploading, send a confirmation message.
        message.edit_text('All files uploaded successfully!')
    else:
        message.edit_text(f'Uploaded {successful_uploads}/{len(files)} files with some errors.')

    # Clear the files list after upload
    context.user_data.clear()
    return ConversationHandler.END


def upload_all_files(files, creds):
    for idx, filepath in enumerate(files, start=1):
        try:
            upload_token = upload_file(filepath, creds.token)
            create_media_item(upload_token, creds.token)
            os.remove(filepath)  # Delete the file after upload
        except Exception as e:
            logger.error(f"Failed to upload {filepath}: {e}")
            return False
    return True

def cancel(update: Update, context: CallbackContext) -> int:
    update.message.reply_text('Operation cancelled. Start over with /start.')
    context.user_data.clear()
    return ConversationHandler.END

def main() -> None:
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            PHOTO_UPLOAD: [
                CommandHandler('done', done),
                MessageHandler(Filters.photo | Filters.video | Filters.document.category("image") | Filters.document.category("video"), photo_video_or_document),
            ],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    dp.add_handler(conv_handler)

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
