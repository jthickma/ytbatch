#!/usr/bin/env sh
set -e

# Usage: download_batch.sh job_id /input/filename.txt
if [ $# -lt 2 ]; then
    echo "Usage: $0 job_id /input/urls.txt" >&2
    exit 1
fi

JOB_ID="$1"
FILE="$2"
WEB_URL="http://ytbatch-web:5000"

if [ ! -f "$FILE" ]; then
    echo "Error: Input file $FILE not found" >&2
    exit 1
fi

user="$(basename "$FILE" .txt)"
BASE_DIR="/downloads/$user"
mkdir -p "$BASE_DIR"

# Function to send progress updates
send_progress() {
    local file_name="$1"
    local status="$2"
    local progress="${3:-null}"
    
    curl -s -X POST "$WEB_URL/api/progress" \
        -H "Content-Type: application/json" \
        -d "{\"job_id\":\"$JOB_ID\",\"file_name\":\"$file_name\",\"status\":\"$status\",\"progress\":$progress}" > /dev/null || true
}

# Function to parse yt-dlp output
parse_output() {
    local line="$1"
    local current_file="$2"
    
    case "$line" in
        *"Downloading webpage"*)
            send_progress "$current_file" "downloading_webpage" 10
            ;;
        *"Downloading video"*|*"Downloading thumbnail"*)
            send_progress "$current_file" "downloading" 30
            ;;
        *"[download]"*"%"*)
            # Extract percentage
            percent=$(echo "$line" | grep -o '[0-9.]*%' | head -1 | tr -d '%')
            if [ -n "$percent" ]; then
                send_progress "$current_file" "downloading" "$percent"
            fi
            ;;
        *"has already been downloaded"*)
            send_progress "$current_file" "completed" 100
            ;;
        *"Deleting original file"*|*"merging formats"*)
            send_progress "$current_file" "processing" 90
            ;;
        *"Download completed"*|*"Finished downloading"*)
            send_progress "$current_file" "completed" 100
            ;;
        *"ERROR:"*|*"Failed"*)
            send_progress "$current_file" "failed" 0
            ;;
    esac
}

# Process URLs one by one
file_count=0
completed_count=0
failed_count=0

while IFS= read -r line || [ -n "$line" ]; do
    clean=$(printf "%s" "$line" | tr -d '\r')
    case "$clean" in
        ""|"#"*) continue ;;
    esac

    # Skip TikTok photo URLs
    if printf "%s" "$clean" | grep -qi '/photo/'; then
        echo "Skipping photo URL: $clean" >&2
        continue
    fi

    file_count=$((file_count + 1))
    current_file="video_$(printf "%03d" "$file_count")"
    
    echo "Processing: $clean" >&2
    send_progress "$current_file" "starting" 0
    
    # Download with progress tracking
    if yt-dlp \
        --ignore-errors \
        --no-warnings \
        --newline \
        -o "${BASE_DIR}/%(upload_date>%Y-%m-%d)s_%(id)s.%(ext)s" \
        --download-archive "${BASE_DIR}/.downloaded.txt" \
        "$clean" 2>&1 | while read -r output_line; do
            echo "$output_line" >&2
            parse_output "$output_line" "$current_file"
        done; then
        completed_count=$((completed_count + 1))
    else
        failed_count=$((failed_count + 1))
    fi
done < "$FILE"

# Send final summary
echo "Download summary: $completed_count completed, $failed_count failed" >&2
send_progress "" "job_summary" $((completed_count * 100 / file_count))