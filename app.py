import streamlit as st
import pandas as pd
import subprocess
import threading
import time
import os
import psutil
import datetime
import pytz
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
import requests
import json

# YouTube API scopes
SCOPES = ['https://www.googleapis.com/auth/youtube.force-ssl']

def get_jakarta_time():
    """Get current time in Jakarta timezone"""
    jakarta_tz = pytz.timezone('Asia/Jakarta')
    return datetime.datetime.now(jakarta_tz)

def format_jakarta_time(dt):
    """Format Jakarta time for display"""
    return dt.strftime('%H:%M WIB')

def get_youtube_service():
    """Get authenticated YouTube service"""
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if os.path.exists('credentials.json'):
                flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
                creds = flow.run_local_server(port=0)
            else:
                st.error("❌ credentials.json file not found! Please upload your YouTube API credentials.")
                return None
        
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    
    return build('youtube', 'v3', credentials=creds)

def create_youtube_broadcast(title, description, start_time_str, privacy_status='public', is_shorts=False):
    """Create YouTube live broadcast with proper time synchronization"""
    try:
        youtube = get_youtube_service()
        if not youtube:
            return None, None, "YouTube service not available"
        
        jakarta_tz = pytz.timezone('Asia/Jakarta')
        
        # Handle different time formats
        if start_time_str == "NOW":
            # For NOW broadcasts, set start time to current time
            start_time = get_jakarta_time()
            scheduled_start_time = start_time.isoformat()
        else:
            try:
                # Parse time string (HH:MM format)
                time_parts = start_time_str.split(':')
                hour = int(time_parts[0])
                minute = int(time_parts[1])
                
                # Create datetime for today with specified time
                now = get_jakarta_time()
                start_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                
                # If time has passed today, schedule for tomorrow
                if start_time <= now:
                    start_time += datetime.timedelta(days=1)
                
                scheduled_start_time = start_time.isoformat()
            except:
                # Fallback to current time
                start_time = get_jakarta_time()
                scheduled_start_time = start_time.isoformat()
        
        # Broadcast snippet
        broadcast_snippet = {
            'title': title,
            'description': description,
            'scheduledStartTime': scheduled_start_time,
        }
        
        # Broadcast status - CRITICAL: Use 'ready' for immediate streams
        if start_time_str == "NOW":
            broadcast_status = {
                'privacyStatus': privacy_status,
                'lifeCycleStatus': 'ready'  # Ready to go live immediately
            }
        else:
            broadcast_status = {
                'privacyStatus': privacy_status,
                'lifeCycleStatus': 'created'  # Scheduled for later
            }
        
        # Create broadcast
        broadcast_response = youtube.liveBroadcasts().insert(
            part='snippet,status,contentDetails',
            body={
                'snippet': broadcast_snippet,
                'status': broadcast_status,
                'contentDetails': {
                    'enableAutoStart': True,
                    'enableAutoStop': True,
                    'recordFromStart': True,
                    'enableDvr': True,
                    'enableContentEncryption': False,
                    'enableEmbed': True,
                    'latencyPreference': 'low'
                }
            }
        ).execute()
        
        broadcast_id = broadcast_response['id']
        
        # Create live stream with proper resolution
        stream_snippet = {
            'title': f"{title} - Stream",
            'description': f"Live stream for {title}"
        }
        
        # Set resolution based on quality
        resolution_map = {
            '240p': '240p',
            '360p': '360p', 
            '480p': '480p',
            '720p': '720p',
            '1080p': '1080p'
        }
        
        stream_cdn = {
            'format': '1080p',  # Default format
            'ingestionType': 'rtmp',
            'resolution': resolution_map.get('720p', '720p'),
            'frameRate': '30fps'
        }
        
        stream_response = youtube.liveStreams().insert(
            part='snippet,cdn',
            body={
                'snippet': stream_snippet,
                'cdn': stream_cdn
            }
        ).execute()
        
        stream_id = stream_response['id']
        stream_key = stream_response['cdn']['ingestionInfo']['streamName']
        
        # Bind broadcast to stream
        youtube.liveBroadcasts().bind(
            part='id,contentDetails',
            id=broadcast_id,
            streamId=stream_id
        ).execute()
        
        # For NOW broadcasts, transition to live immediately
        if start_time_str == "NOW":
            try:
                # Wait a moment for binding to complete
                time.sleep(2)
                
                # Transition to testing state first
                youtube.liveBroadcasts().transition(
                    broadcastStatus='testing',
                    id=broadcast_id,
                    part='id,status'
                ).execute()
                
                st.success(f"✅ Broadcast created and ready to go live!")
                
            except Exception as e:
                st.warning(f"⚠️ Broadcast created but transition failed: {str(e)}")
        
        return broadcast_id, stream_key, None
        
    except HttpError as e:
        error_details = e.error_details[0] if e.error_details else {}
        return None, None, f"YouTube API Error: {error_details.get('message', str(e))}"
    except Exception as e:
        return None, None, f"Error creating broadcast: {str(e)}"

def start_youtube_broadcast(broadcast_id):
    """Start YouTube broadcast - transition from testing to live"""
    try:
        youtube = get_youtube_service()
        if not youtube:
            return False, "YouTube service not available"
        
        # Get current broadcast status
        broadcast_response = youtube.liveBroadcasts().list(
            part='status,snippet',
            id=broadcast_id
        ).execute()
        
        if not broadcast_response['items']:
            return False, "Broadcast not found"
        
        current_status = broadcast_response['items'][0]['status']['lifeCycleStatus']
        
        # Transition based on current status
        if current_status == 'ready':
            # Transition to testing first
            youtube.liveBroadcasts().transition(
                broadcastStatus='testing',
                id=broadcast_id,
                part='id,status'
            ).execute()
            time.sleep(3)  # Wait for transition
            
            # Then transition to live
            youtube.liveBroadcasts().transition(
                broadcastStatus='live',
                id=broadcast_id,
                part='id,status'
            ).execute()
            
        elif current_status == 'testing':
            # Direct transition to live
            youtube.liveBroadcasts().transition(
                broadcastStatus='live',
                id=broadcast_id,
                part='id,status'
            ).execute()
        
        return True, "Broadcast started successfully"
        
    except HttpError as e:
        error_details = e.error_details[0] if e.error_details else {}
        return False, f"Failed to start broadcast: {error_details.get('message', str(e))}"
    except Exception as e:
        return False, f"Error starting broadcast: {str(e)}"

def stop_youtube_broadcast(broadcast_id):
    """Stop YouTube broadcast"""
    try:
        youtube = get_youtube_service()
        if not youtube:
            return False, "YouTube service not available"
        
        youtube.liveBroadcasts().transition(
            broadcastStatus='complete',
            id=broadcast_id,
            part='id,status'
        ).execute()
        
        return True, "Broadcast stopped successfully"
        
    except Exception as e:
        return False, f"Error stopping broadcast: {str(e)}"

def upload_thumbnail(video_id, thumbnail_path):
    """Upload thumbnail to YouTube video"""
    try:
        youtube = get_youtube_service()
        if not youtube:
            return False, "YouTube service not available"
        
        if not os.path.exists(thumbnail_path):
            return False, "Thumbnail file not found"
        
        youtube.thumbnails().set(
            videoId=video_id,
            media_body=MediaFileUpload(thumbnail_path)
        ).execute()
        
        return True, "Thumbnail uploaded successfully"
        
    except HttpError as e:
        if e.resp.status == 429:
            return False, "Rate limit exceeded. Please try again later."
        error_details = e.error_details[0] if e.error_details else {}
        return False, f"Failed to upload thumbnail: {error_details.get('message', str(e))}"
    except Exception as e:
        return False, f"Error uploading thumbnail: {str(e)}"

# Initialize session state
if 'streams' not in st.session_state:
    st.session_state.streams = pd.DataFrame(columns=[
        'Video', 'Streaming Key', 'Jam Mulai', 'Status', 'PID', 'Is Shorts', 'Quality', 'Broadcast ID'
    ])

if 'processes' not in st.session_state:
    st.session_state.processes = {}

def save_persistent_streams(df):
    """Save streams to persistent storage"""
    try:
        df.to_csv('persistent_streams.csv', index=False)
    except Exception as e:
        st.error(f"Error saving streams: {e}")

def load_persistent_streams():
    """Load streams from persistent storage"""
    try:
        if os.path.exists('persistent_streams.csv'):
            df = pd.read_csv('persistent_streams.csv')
            # Ensure all required columns exist
            required_columns = ['Video', 'Streaming Key', 'Jam Mulai', 'Status', 'PID', 'Is Shorts', 'Quality', 'Broadcast ID']
            for col in required_columns:
                if col not in df.columns:
                    df[col] = '' if col in ['Video', 'Streaming Key', 'Jam Mulai', 'Status', 'Broadcast ID'] else False if col == 'Is Shorts' else '720p' if col == 'Quality' else 0
            return df
        else:
            return pd.DataFrame(columns=['Video', 'Streaming Key', 'Jam Mulai', 'Status', 'PID', 'Is Shorts', 'Quality', 'Broadcast ID'])
    except Exception as e:
        st.error(f"Error loading streams: {e}")
        return pd.DataFrame(columns=['Video', 'Streaming Key', 'Jam Mulai', 'Status', 'PID', 'Is Shorts', 'Quality', 'Broadcast ID'])

# Load persistent streams on startup
if st.session_state.streams.empty:
    st.session_state.streams = load_persistent_streams()

def run_ffmpeg(video_path, streaming_key, is_shorts=False, stream_index=None, quality='720p', broadcast_id=None):
    """Run FFmpeg with proper YouTube streaming settings"""
    try:
        # Quality settings
        quality_settings = {
            '240p': {'resolution': '426x240', 'bitrate': '400k', 'fps': '24'},
            '360p': {'resolution': '640x360', 'bitrate': '800k', 'fps': '24'},
            '480p': {'resolution': '854x480', 'bitrate': '1200k', 'fps': '30'},
            '720p': {'resolution': '1280x720', 'bitrate': '2500k', 'fps': '30'},
            '1080p': {'resolution': '1920x1080', 'bitrate': '4500k', 'fps': '30'}
        }
        
        settings = quality_settings.get(quality, quality_settings['720p'])
        
        # FFmpeg command for YouTube streaming
        cmd = [
            'ffmpeg',
            '-re',  # Read input at native frame rate
            '-i', video_path,
            '-c:v', 'libx264',  # Video codec
            '-preset', 'veryfast',  # Encoding speed
            '-tune', 'zerolatency',  # Low latency
            '-b:v', settings['bitrate'],  # Video bitrate
            '-maxrate', settings['bitrate'],
            '-bufsize', str(int(settings['bitrate'].replace('k', '')) * 2) + 'k',
            '-s', settings['resolution'],  # Resolution
            '-r', settings['fps'],  # Frame rate
            '-g', '60',  # GOP size
            '-keyint_min', '60',
            '-sc_threshold', '0',
            '-c:a', 'aac',  # Audio codec
            '-b:a', '128k',  # Audio bitrate
            '-ar', '44100',  # Audio sample rate
            '-ac', '2',  # Audio channels
            '-f', 'flv',  # Output format
            f'rtmp://a.rtmp.youtube.com/live2/{streaming_key}'
        ]
        
        # Start FFmpeg process
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True
        )
        
        # Store process info
        if stream_index is not None:
            st.session_state.processes[stream_index] = process
            st.session_state.streams.loc[stream_index, 'PID'] = process.pid
            st.session_state.streams.loc[stream_index, 'Status'] = 'Sedang Live'
            save_persistent_streams(st.session_state.streams)
        
        # Auto-start YouTube broadcast if broadcast_id is provided
        if broadcast_id:
            def start_broadcast_delayed():
                time.sleep(8)  # Wait for stream to establish
                success, message = start_youtube_broadcast(broadcast_id)
                if success:
                    print(f"✅ YouTube broadcast started: {message}")
                else:
                    print(f"❌ Failed to start YouTube broadcast: {message}")
            
            # Start broadcast in background thread
            threading.Thread(target=start_broadcast_delayed, daemon=True).start()
        
        # Monitor process
        def monitor_process():
            try:
                stdout, stderr = process.communicate()
                if stream_index is not None and stream_index in st.session_state.processes:
                    del st.session_state.processes[stream_index]
                    st.session_state.streams.loc[stream_index, 'Status'] = 'Selesai'
                    st.session_state.streams.loc[stream_index, 'PID'] = 0
                    save_persistent_streams(st.session_state.streams)
                    
                    # Auto-stop YouTube broadcast
                    if broadcast_id:
                        stop_youtube_broadcast(broadcast_id)
                        
            except Exception as e:
                print(f"Error monitoring process: {e}")
        
        # Start monitoring in background thread
        threading.Thread(target=monitor_process, daemon=True).start()
        
        return True
        
    except Exception as e:
        st.error(f"Error starting stream: {e}")
        return False

def start_stream(video_path, streaming_key, is_shorts=False, stream_index=None, quality='720p', broadcast_id=None):
    """Start streaming with proper error handling"""
    if not os.path.exists(video_path):
        st.error(f"❌ Video file not found: {video_path}")
        return False
    
    return run_ffmpeg(video_path, streaming_key, is_shorts, stream_index, quality, broadcast_id)

def stop_stream(stream_index):
    """Stop streaming process"""
    try:
        if stream_index in st.session_state.processes:
            process = st.session_state.processes[stream_index]
            
            # Get broadcast ID for cleanup
            broadcast_id = st.session_state.streams.loc[stream_index, 'Broadcast ID']
            
            # Terminate FFmpeg process
            process.terminate()
            time.sleep(2)
            
            if process.poll() is None:
                process.kill()
            
            # Clean up
            del st.session_state.processes[stream_index]
            st.session_state.streams.loc[stream_index, 'Status'] = 'Dihentikan'
            st.session_state.streams.loc[stream_index, 'PID'] = 0
            save_persistent_streams(st.session_state.streams)
            
            # Stop YouTube broadcast
            if broadcast_id and broadcast_id != '':
                stop_youtube_broadcast(broadcast_id)
            
            return True
    except Exception as e:
        st.error(f"Error stopping stream: {e}")
        return False

def check_scheduled_streams():
    """Check and start scheduled streams"""
    jakarta_time = get_jakarta_time()
    current_time = format_jakarta_time(jakarta_time)
    
    for idx, row in st.session_state.streams.iterrows():
        if row['Status'] == 'Menunggu':
            start_time = row['Jam Mulai']
            
            # Handle "NOW" case - start immediately
            if start_time == "NOW":
                quality = row.get('Quality', '720p')
                broadcast_id = row.get('Broadcast ID', None)
                if start_stream(row['Video'], row['Streaming Key'], row.get('Is Shorts', False), idx, quality, broadcast_id):
                    st.session_state.streams.loc[idx, 'Jam Mulai'] = current_time
                    save_persistent_streams(st.session_state.streams)
                continue
            
            # Handle scheduled time
            try:
                # Parse scheduled time
                scheduled_parts = start_time.replace(' WIB', '').split(':')
                scheduled_hour = int(scheduled_parts[0])
                scheduled_minute = int(scheduled_parts[1])
                
                # Current time
                current_hour = jakarta_time.hour
                current_minute = jakarta_time.minute
                
                # Check if it's time to start
                if (current_hour > scheduled_hour or 
                    (current_hour == scheduled_hour and current_minute >= scheduled_minute)):
                    
                    quality = row.get('Quality', '720p')
                    broadcast_id = row.get('Broadcast ID', None)
                    if start_stream(row['Video'], row['Streaming Key'], row.get('Is Shorts', False), idx, quality, broadcast_id):
                        st.session_state.streams.loc[idx, 'Jam Mulai'] = current_time
                        save_persistent_streams(st.session_state.streams)
                        
            except Exception as e:
                st.error(f"Error processing scheduled stream: {e}")

def get_video_files():
    """Get list of video files from current directory"""
    video_extensions = ['.mp4', '.avi', '.mov', '.mkv', '.flv', '.wmv', '.webm']
    video_files = []
    
    try:
        for file in os.listdir('.'):
            if any(file.lower().endswith(ext) for ext in video_extensions):
                video_files.append(file)
    except Exception as e:
        st.error(f"Error reading video files: {e}")
    
    return sorted(video_files)

def calculate_time_difference(target_time_str):
    """Calculate time difference for display"""
    try:
        if target_time_str == "NOW":
            return "Starting now..."
        
        jakarta_time = get_jakarta_time()
        
        # Parse target time
        time_parts = target_time_str.replace(' WIB', '').split(':')
        target_hour = int(time_parts[0])
        target_minute = int(time_parts[1])
        
        # Create target datetime for today
        target_time = jakarta_time.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
        
        # If target time has passed today, it's for tomorrow
        if target_time <= jakarta_time:
            target_time += datetime.timedelta(days=1)
        
        # Calculate difference
        time_diff = target_time - jakarta_time
        
        if time_diff.total_seconds() < 60:
            return "Starting soon..."
        elif time_diff.total_seconds() < 3600:
            minutes = int(time_diff.total_seconds() / 60)
            return f"Will start in {minutes} minutes"
        else:
            hours = int(time_diff.total_seconds() / 3600)
            minutes = int((time_diff.total_seconds() % 3600) / 60)
            return f"Will start in {hours}h {minutes}m"
            
    except Exception:
        return "Time calculation error"

# Streamlit UI
st.set_page_config(page_title="🎬 YouTube Live Stream Manager", layout="wide")

st.title("🎬 YouTube Live Stream Manager")
st.markdown("---")

# Auto-refresh for scheduled streams
check_scheduled_streams()

# Sidebar for YouTube Broadcast Creation
with st.sidebar:
    st.header("📺 Create YouTube Broadcast")
    
    with st.form("broadcast_form"):
        title = st.text_input("🎬 Broadcast Title", value="Live Stream")
        description = st.text_area("📝 Description", value="Live streaming content")
        
        # Privacy settings
        privacy = st.selectbox("🔒 Privacy", ['public', 'unlisted', 'private'], index=0)
        
        # Time selection with Jakarta timezone
        jakarta_time = get_jakarta_time()
        current_time_str = format_jakarta_time(jakarta_time)
        
        st.write(f"🕐 Current Time: **{current_time_str}**")
        
        # Quick time buttons
        col1, col2, col3, col4 = st.columns(4)
        
        start_immediately = False
        broadcast_time = None
        
        with col1:
            if st.form_submit_button("🚀 NOW"):
                broadcast_time = jakarta_time.time()
                start_immediately = True
        
        with col2:
            if st.form_submit_button("⏰ +5min"):
                future_time = jakarta_time + datetime.timedelta(minutes=5)
                broadcast_time = future_time.time()
        
        with col3:
            if st.form_submit_button("⏰ +15min"):
                future_time = jakarta_time + datetime.timedelta(minutes=15)
                broadcast_time = future_time.time()
        
        with col4:
            if st.form_submit_button("⏰ +30min"):
                future_time = jakarta_time + datetime.timedelta(minutes=30)
                broadcast_time = future_time.time()
        
        # Manual time input
        if not broadcast_time:
            manual_time = st.time_input("🕐 Or set custom time", value=jakarta_time.time())
            if st.form_submit_button("📅 Schedule"):
                broadcast_time = manual_time
        
        # Process broadcast creation
        if broadcast_time:
            with st.spinner("Creating YouTube broadcast..."):
                # Format time for API
                if start_immediately:
                    time_str = "NOW"
                else:
                    time_str = broadcast_time.strftime('%H:%M')
                
                # Create broadcast
                broadcast_id, stream_key, error = create_youtube_broadcast(
                    title, description, time_str, privacy
                )
                
                if error:
                    st.error(f"❌ {error}")
                else:
                    st.success(f"✅ Broadcast created successfully!")
                    st.info(f"🔑 Stream Key: `{stream_key}`")
                    st.info(f"🆔 Broadcast ID: `{broadcast_id}`")
                    
                    # Auto-add to stream manager
                    video_files = get_video_files()
                    if video_files:
                        selected_video = st.selectbox("📹 Select video to stream", video_files)
                        quality = st.selectbox("🎥 Quality", ['240p', '360p', '480p', '720p', '1080p'], index=3)
                        is_shorts = st.checkbox("📱 YouTube Shorts format")
                        
                        if st.button("➕ Add to Stream Manager"):
                            # Add to streams
                            new_stream = pd.DataFrame({
                                'Video': [selected_video],
                                'Streaming Key': [stream_key],
                                'Jam Mulai': [time_str],
                                'Status': ['Menunggu'],
                                'PID': [0],
                                'Is Shorts': [is_shorts],
                                'Quality': [quality],
                                'Broadcast ID': [broadcast_id]
                            })
                            
                            st.session_state.streams = pd.concat([st.session_state.streams, new_stream], ignore_index=True)
                            save_persistent_streams(st.session_state.streams)
                            st.success("✅ Stream added to manager!")
                            st.rerun()

# Main content area
col1, col2 = st.columns([2, 1])

with col1:
    st.header("📋 Stream Manager")
    
    # Add new stream form
    with st.expander("➕ Add New Stream", expanded=False):
        with st.form("add_stream"):
            video_files = get_video_files()
            
            if not video_files:
                st.warning("⚠️ No video files found in current directory")
                st.stop()
            
            selected_video = st.selectbox("📹 Select Video", video_files)
            streaming_key = st.text_input("🔑 Streaming Key", help="Your YouTube streaming key")
            
            # Time input with Jakarta timezone
            jakarta_time = get_jakarta_time()
            current_time_str = format_jakarta_time(jakarta_time)
            
            st.write(f"🕐 Current Time: **{current_time_str}**")
            
            # Quick time selection
            col_now, col_5, col_15, col_30 = st.columns(4)
            
            schedule_time = None
            
            with col_now:
                if st.form_submit_button("🚀 NOW"):
                    schedule_time = "NOW"
            
            with col_5:
                if st.form_submit_button("⏰ +5min"):
                    future_time = jakarta_time + datetime.timedelta(minutes=5)
                    schedule_time = format_jakarta_time(future_time)
            
            with col_15:
                if st.form_submit_button("⏰ +15min"):
                    future_time = jakarta_time + datetime.timedelta(minutes=15)
                    schedule_time = format_jakarta_time(future_time)
            
            with col_30:
                if st.form_submit_button("⏰ +30min"):
                    future_time = jakarta_time + datetime.timedelta(minutes=30)
                    schedule_time = format_jakarta_time(future_time)
            
            # Manual time input
            if not schedule_time:
                manual_time = st.time_input("🕐 Or set custom time", value=jakarta_time.time())
                quality = st.selectbox("🎥 Quality", ['240p', '360p', '480p', '720p', '1080p'], index=3)
                is_shorts = st.checkbox("📱 YouTube Shorts format")
                
                if st.form_submit_button("📅 Add Stream"):
                    schedule_time = format_jakarta_time(
                        jakarta_time.replace(hour=manual_time.hour, minute=manual_time.minute, second=0, microsecond=0)
                    )
            
            # Process stream addition
            if schedule_time and streaming_key:
                new_stream = pd.DataFrame({
                    'Video': [selected_video],
                    'Streaming Key': [streaming_key],
                    'Jam Mulai': [schedule_time],
                    'Status': ['Menunggu'],
                    'PID': [0],
                    'Is Shorts': [is_shorts if 'is_shorts' in locals() else False],
                    'Quality': [quality if 'quality' in locals() else '720p'],
                    'Broadcast ID': ['']
                })
                
                st.session_state.streams = pd.concat([st.session_state.streams, new_stream], ignore_index=True)
                save_persistent_streams(st.session_state.streams)
                st.success("✅ Stream added successfully!")
                st.rerun()

    # Display streams
    if not st.session_state.streams.empty:
        st.subheader("📺 Active Streams")
        
        for idx, row in st.session_state.streams.iterrows():
            with st.container():
                # Create card-like layout
                card_col1, card_col2, card_col3, card_col4 = st.columns([3, 2, 2, 2])
                
                with card_col1:
                    st.write(f"**📹 {row['Video']}**")
                    st.caption(f"Duration: 01:00:00 | Quality: {row.get('Quality', '720p')}")
                    
                    # YouTube link if broadcast ID exists
                    if row.get('Broadcast ID') and row['Broadcast ID'] != '':
                        youtube_url = f"https://youtube.com/watch?v={row['Broadcast ID']}"
                        st.markdown(f"🔗 [Watch on YouTube]({youtube_url})")
                    
                    st.caption(f"Key: {row['Streaming Key'][:8]}****")
                
                with card_col2:
                    # Time display with countdown
                    st.write(f"🕐 **{row['Jam Mulai']}**")
                    if row['Status'] == 'Menunggu':
                        time_info = calculate_time_difference(row['Jam Mulai'])
                        st.caption(time_info)
                
                with card_col3:
                    # Status with colored indicators
                    status = row['Status']
                    if status == 'Sedang Live':
                        st.success(f"🟢 {status}")
                    elif status == 'Menunggu':
                        st.warning(f"🟡 {status}")
                    elif status == 'Selesai':
                        st.info(f"🔵 {status}")
                    else:
                        st.error(f"🔴 {status}")
                
                with card_col4:
                    # Action buttons
                    if row['Status'] == 'Menunggu':
                        if st.button(f"▶️ Start Now", key=f"start_{idx}"):
                            quality = row.get('Quality', '720p')
                            broadcast_id = row.get('Broadcast ID', None)
                            if start_stream(row['Video'], row['Streaming Key'], row.get('Is Shorts', False), idx, quality, broadcast_id):
                                st.session_state.streams.loc[idx, 'Status'] = 'Sedang Live'
                                st.session_state.streams.loc[idx, 'Jam Mulai'] = format_jakarta_time(get_jakarta_time())
                                save_persistent_streams(st.session_state.streams)
                                st.rerun()
                    
                    elif row['Status'] == 'Sedang Live':
                        if st.button(f"⏹️ Stop Stream", key=f"stop_{idx}"):
                            if stop_stream(idx):
                                st.rerun()
                    
                    # Delete button
                    if st.button(f"🗑️ Delete", key=f"delete_{idx}"):
                        if row['Status'] == 'Sedang Live':
                            stop_stream(idx)
                        st.session_state.streams = st.session_state.streams.drop(idx).reset_index(drop=True)
                        save_persistent_streams(st.session_state.streams)
                        st.rerun()
                
                st.markdown("---")
    else:
        st.info("📝 No streams configured. Add a stream to get started!")

with col2:
    st.header("📊 System Status")
    
    # Current time
    jakarta_time = get_jakarta_time()
    st.metric("🕐 Current Time", format_jakarta_time(jakarta_time))
    
    # Active streams count
    active_streams = len(st.session_state.streams[st.session_state.streams['Status'] == 'Sedang Live'])
    st.metric("📺 Active Streams", active_streams)
    
    # Waiting streams count
    waiting_streams = len(st.session_state.streams[st.session_state.streams['Status'] == 'Menunggu'])
    st.metric("⏳ Waiting Streams", waiting_streams)
    
    # System resources
    try:
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        
        st.metric("💻 CPU Usage", f"{cpu_percent:.1f}%")
        st.metric("🧠 Memory Usage", f"{memory.percent:.1f}%")
    except:
        st.info("System monitoring unavailable")
    
    # Auto-refresh
    if st.button("🔄 Refresh Status"):
        st.rerun()
    
    # Auto-refresh every 30 seconds
    time.sleep(1)
    st.rerun()

# Footer
st.markdown("---")
st.markdown("🎬 **YouTube Live Stream Manager** - Automated streaming with Jakarta timezone support")
