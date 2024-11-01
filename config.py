import os

# Create downloads directory in the current working directory
DOWNLOAD_DIR = os.path.join(os.getcwd(), "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Maximum number of concurrent downloads
MAX_CONCURRENT_DOWNLOADS = 3

# YT-DLP Configuration
YTDLP_OPTIONS = {
    'format': 'bestaudio/best',
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'wav',
        'preferredquality': '192',
    }],
    'quiet': False,
    'no_warnings': False,
    'extract_flat': False,
    'keepvideo': False,
    'writethumbnail': False,
    'prefer_ffmpeg': True,
    'verbose': True,
    'postprocessor_args': [
        '-ar', '44100',
        '-ac', '2',
    ],
}

# File naming template
FILENAME_TEMPLATE = "{artist} - {title}"

# Supported formats
SUPPORTED_FORMATS = ['mp3', 'wav']
