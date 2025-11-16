# Real-time Batch Downloader

A modern, real-time batch downloader with WebSocket-based progress tracking, configuration management, and an enhanced web UI.

## Features

### 🚀 Real-time Updates
- **Live Progress Tracking**: Monitor download progress for each file in real-time
- **WebSocket Integration**: Instant updates without page refresh
- **File-by-file Status**: See which files are queued, downloading, completed, or failed

### 🎨 Modern Web UI
- **Dark Theme**: Professional dark interface with blue accents
- **Responsive Design**: Works on desktop and mobile devices
- **Interactive Dashboard**: Real-time statistics and job management
- **Drag & Drop Upload**: Easy file upload with visual feedback

### ⚙️ Configuration Management
- **Quality Settings**: Choose download quality (Best, 720p, 480p, etc.)
- **Concurrent Downloads**: Control maximum simultaneous downloads
- **Audio Extraction**: Option to extract audio from videos
- **Auto-cleanup**: Automatically remove completed jobs
- **Notifications**: Enable/disable desktop notifications

### 📊 Advanced Features
- **Job Management**: Cancel, retry, or delete jobs
- **File Browser**: Browse and download completed files
- **Statistics Dashboard**: View job and file statistics
- **Error Handling**: Detailed error reporting and retry mechanisms

## Quick Start

### Prerequisites
- Docker and Docker Compose
- Modern web browser

### Installation

1. Clone or download the project files
2. Navigate to the project directory
3. Start the services:

```bash
docker-compose up -d
```

4. Access the web interface at `http://localhost:8899`

## Usage

### Adding Download Jobs

1. **Prepare URL List**: Create a `.txt` file with one URL per line
   ```
   https://www.tiktok.com/@user/video/1234567890
   https://www.youtube.com/watch?v=abcdefg
   # This is a comment
   https://example.com/video2
   ```

2. **Upload File**: 
   - Drag and drop the `.txt` file onto the upload area
   - Or click the upload area to browse for files
   - Or use the file input button

3. **Monitor Progress**: Watch real-time progress updates in the Jobs section

### Managing Jobs

- **View Status**: Jobs show as Queued, Running, Completed, Failed, or Cancelled
- **Cancel Job**: Click the Cancel button on queued jobs
- **Retry Job**: Click Retry on failed or cancelled jobs  
- **Delete Job**: Remove completed jobs from the list

### Configuration

Click the Settings button to configure:

- **Max Concurrent Downloads**: Limit simultaneous downloads (1-10)
- **Download Quality**: Choose video quality preference
- **Audio Extraction**: Extract audio tracks from videos
- **Auto-cleanup**: Automatically remove completed jobs
- **Notifications**: Enable desktop notifications for job completion

### Downloading Files

Completed files appear in the "Downloaded Files" section:
- Files are organized by job name
- Click the Download button to save individual files
- View file size and modification date

## System Architecture

### Components

1. **Web Interface** (`ytbatch-web`):
   - Flask application with Socket.IO
   - Real-time WebSocket communication
   - REST API for job management
   - Modern responsive UI

2. **Worker Service** (`ytbatch-worker`):
   - Background job processing
   - yt-dlp integration
   - Progress tracking and reporting
   - Concurrent download management

3. **Data Storage**:
   - Job queue: JSON file with job status
   - Configuration: JSON file with user settings
   - Downloads: Organized by job in `/downloads`

### File Structure

```
ytbatch/
├── data/
│   ├── input/          # Upload URL lists here
│   ├── downloads/      # Completed downloads
│   └── queue/          # Job queue and config
├── web/                # Web interface
│   ├── app.py         # Flask + Socket.IO application
│   └── Dockerfile     # Web service container
├── worker/             # Background worker
│   ├── worker.py      # Main worker logic
│   ├── download_batch.sh  # Download script
│   └── Dockerfile     # Worker container
└── docker-compose.yml  # Service orchestration
```

## API Endpoints

### Job Management
- `GET /api/jobs` - List all jobs
- `POST /api/jobs` - Create new job (file upload)
- `GET /api/jobs/<id>` - Get specific job
- `POST /api/jobs/<id>/cancel` - Cancel queued job
- `POST /api/jobs/<id>/retry` - Retry failed job
- `DELETE /api/jobs/<id>` - Delete job

### Configuration
- `GET /api/config` - Get current configuration
- `PUT /api/config` - Update configuration

### Files
- `GET /api/files` - List downloaded files
- `GET /download/<user>/<file>` - Download specific file

### Real-time Updates
- WebSocket connection for live progress updates
- Progress events: `progress_update`, `new_job`, `job_completed`

## Supported Sites

Uses yt-dlp which supports thousands of sites including:
- TikTok (videos only, photos not supported)
- YouTube
- Vimeo
- Twitter
- Instagram
- Facebook
- And many more...

## Troubleshooting

### Common Issues

1. **Downloads fail**:
   - Check URL validity
   - Verify site is supported by yt-dlp
   - Check worker logs: `docker logs ytbatch-worker`

2. **Progress not updating**:
   - Ensure WebSocket connection is established
   - Check browser console for errors
   - Verify worker can reach web service

3. **Files not appearing**:
   - Check download directory permissions
   - Verify job completed successfully
   - Check available disk space

### Logs

View service logs:
```bash
# Web interface logs
docker logs ytbatch-web

# Worker logs  
docker logs ytbatch-worker

# Follow logs in real-time
docker logs -f ytbatch-worker
```

## Development

### Adding Features

1. **Web UI**: Modify `/web/app.py` and the HTML template
2. **Worker Logic**: Update `/worker/worker.py` and `/worker/download_batch.sh`
3. **API Endpoints**: Add new routes in the Flask app
4. **Real-time Events**: Use Socket.IO for live updates

### Environment Variables

- `YTBATCH_POLL_INTERVAL`: Worker polling interval (default: 3 seconds)
- `MAX_CONCURRENT_DOWNLOADS`: Maximum simultaneous downloads (default: 3)
- `FLASK_ENV`: Flask environment (production/development)

## License

This project is open source. Feel free to modify and distribute according to your needs.

## Contributing

Contributions are welcome! Please feel free to submit issues and pull requests.