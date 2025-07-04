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

def get_channel_credentials_path(channel_name):
    """Get credentials file path for specific channel"""
    if channel_name == 'default':
        return 'credentials.json'
    return f'credentials_{channel_name}.json'

def get_channel_token_path(channel_name):
    """Get token file path for specific channel"""
    if channel_name == 'default':
        return 'token.json'
    return f'token_{channel_name}.json'

def get_youtube_service(channel_name='default'):
    """Get authenticated YouTube service for specific channel"""
    try:
        creds = None
        token_path = get_channel_token_path(channel_name)
        credentials_path = get_channel_credentials_path(channel_name)
        
        if os.path.exists(token_path):
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except Exception as e:
                    st.error(f"❌ Token refresh failed for channel '{channel_name}': {str(e)}")
                    return None
            else:
                if os.path.exists(credentials_path):
                    try:
                        flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
                        creds = flow.run_local_server(port=0)
                    except Exception as e:
                        st.error(f"❌ Authentication failed for channel '{channel_name}': {str(e)}")
                        return None
                else:
                    st.warning(f"⚠️ {credentials_path} file not found! Please upload your YouTube API credentials for channel '{channel_name}'.")
                    return None
            
            # Save credentials
            try:
                with open(token_path, 'w') as token:
                    token.write(creds.to_json())
            except Exception as e:
                st.error(f"❌ Failed to save token for channel '{channel_name}': {str(e)}")
        
        return build('youtube', 'v3', credentials=creds)
    
    except Exception as e:
        st.error(f"❌ Error creating YouTube service for channel '{channel_name}': {str(e)}")
        return None

def get_channel_info(channel_name='default'):
    """Get channel information"""
    try:
        youtube = get_youtube_service(channel_name)
        if not youtube:
            return None
        
        response = youtube.channels().list(
            part='snippet,statistics',
            mine=True
        ).execute()
        
        if response['items']:
            channel = response['items'][0]
            return {
                'title': channel['snippet']['title'],
                'id': channel['id'],
                'subscribers': channel['statistics'].get('subscriberCount', 'N/A'),
                'videos': channel['statistics'].get('videoCount', 'N/A')
            }
        return None
    except Exception as e:
        st.error(f"❌ Error getting channel info for '{channel_name}': {str(e)}")
        return None

def create_youtube_broadcast(title, description, start_time_str, privacy_status='public', is_shorts=False, channel_name='default'):
    """Create YouTube live broadcast with proper time synchronization"""
    try:
        youtube = get_youtube_service(channel_name)
        if not youtube:
            return None, None, f"YouTube service not available for channel '{channel_name}'"
        
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
                
                st.success(f"✅ Broadcast created and ready to go live on channel '{channel_name}'!")
                
            except Exception as e:
                st.warning(f"⚠️ Broadcast created but transition failed: {str(e)}")
        
        return broadcast_id, stream_key, None
        
    except HttpError as e:
        error_details = e.error_details[0] if e.error_details else {}
        return None, None, f"YouTube API Error for channel '{channel_name}': {error_details.get('message', str(e))}"
    except Exception as e:
        return None, None, f"Error creating broadcast for channel '{channel_name}': {str(e)}"

def start_youtube_broadcast(broadcast_id, channel_name='default'):
    """Start YouTube broadcast - transition from testing to live"""
    try:
        youtube = get_youtube_service(channel_name)
        if not youtube:
            return False, f"YouTube service not available for channel '{channel_name}'"
        
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
        
        return True, f"Broadcast started successfully on channel '{channel_name}'"
        
    except HttpError as e:
        error_details = e.error_details[0] if e.error_details else {}
        return False, f"Failed to start broadcast on channel '{channel_name}': {error_details.get('message', str(e))}"
    except Exception as e:
        return False, f"Error starting broadcast on channel '{channel_name}': {str(e)}"

def stop_youtube_broadcast(broadcast_id, channel_name='default'):
    """Stop YouTube broadcast"""
    try:
        youtube = get_youtube_service(channel_name)
        if not youtube:
            return False, f"YouTube service not available for channel '{channel_name}'"
        
        youtube.liveBroadcasts().transition(
            broadcastStatus='complete',
            id=broadcast_id,
            part='id,status'
        ).execute()
        
        return True, f"Broadcast stopped successfully on channel '{channel_name}'"
        
    except Exception as e:
        return False, f"Error stopping broadcast on channel '{channel_name}': {str(e)}"

def upload_thumbnail(video_id, thumbnail_path, channel_name='default'):
    """Upload thumbnail to YouTube video"""
    try:
        youtube = get_youtube_service(channel_name)
        if not youtube:
            return False, f"YouTube service not available for channel '{channel_name}'"
        
        if not os.path.exists(thumbnail_path):
            return False, "Thumbnail file not found"
        
        youtube.thumbnails().set(
            videoId=video_id,
            media_body=MediaFileUpload(thumbnail_path)
        ).execute()
        
        return True, f"Thumbnail uploaded successfully to channel '{channel_name}'"
        
    except HttpError as e:
        if e.resp.status == 429:
            return False, "Rate limit exceeded. Please try again later."
        error_details = e.error_details[0] if e.error_details else {}
        return False, f"Failed to upload thumbnail to channel '{channel_name}': {error_details.get('message', str(e))}"
    except Exception as e:
        return False, f"Error uploading thumbnail to channel '{channel_name}': {str(e)}"

def get_available_channels():
    """Get list of available channels based on credentials files"""
    channels = []
    
    try:
        # Check for default credentials
        if os.path.exists('credentials.json'):
            channels.append('default')
        
        # Check for named channel credentials
        for file in os.listdir('.'):
            if file.startswith('credentials_') and file.endswith('.json'):
                channel_name = file.replace('credentials_', '').replace('.json', '')
                if channel_name not in channels:
                    channels.append(channel_name)
    except Exception as e:
        st.error(f"Error scanning for channels: {e}")
    
    return sorted(channels)

def save_channel_config():
    """Save channel configuration"""
    try:
        config = {
            'channels': st.session_state.get('channel_configs', {})
        }
        with open('channel_config.json', 'w') as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        st.error(f"Error saving channel config: {e}")

def load_channel_config():
    """Load channel configuration"""
    try:
        if os.path.exists('channel_config.json'):
            with open('channel_config.json', 'r') as f:
                config = json.load(f)
                return config.get('channels', {})
        return {}
    except Exception as e:
        st.error(f"Error loading channel config: {e}")
        return {}

def save_stream_config(streams_df):
    """Save stream configuration to JSON"""
    try:
        streams_data = streams_df.to_dict('records')
        config = {
            'streams': streams_data,
            'last_updated': datetime.datetime.now().isoformat()
        }
        with open('streams_config.json', 'w') as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        st.error(f"Error saving stream config: {e}")

def load_stream_config():
    """Load stream configuration from JSON"""
    try:
        if os.path.exists('streams_config.json'):
            with open('streams_config.json', 'r') as f:
                config = json.load(f)
                streams_data = config.get('streams', [])
                if streams_data:
                    df = pd.DataFrame(streams_data)
                    # Ensure all required columns exist
                    required_columns = ['Video', 'Streaming Key', 'Jam Mulai', 'Status', 'PID', 'Is Shorts', 'Quality', 'Broadcast ID', 'Channel']
                    for col in required_columns:
                        if col not in df.columns:
                            if col == 'Channel':
                                df[col] = 'default'
                            elif col in ['Video', 'Streaming Key', 'Jam Mulai', 'Status', 'Broadcast ID']:
                                df[col] = ''
                            elif col == 'Is Shorts':
                                df[col] = False
                            elif col == 'Quality':
                                df[col] = '720p'
                            else:
                                df[col] = 0
                    return df
        return pd.DataFrame(columns=['Video', 'Streaming Key', 'Jam Mulai', 'Status', 'PID', 'Is Shorts', 'Quality', 'Broadcast ID', 'Channel'])
    except Exception as e:
        st.error(f"Error loading stream config: {e}")
        return pd.DataFrame(columns=['Video', 'Streaming Key', 'Jam Mulai', 'Status', 'PID', 'Is Shorts', 'Quality', 'Broadcast ID', 'Channel'])

# Initialize session state
if 'streams' not in st.session_state:
    st.session_state.streams = load_stream_config()

if 'processes' not in st.session_state:
    st.session_state.processes = {}

if 'channel_configs' not in st.session_state:
    st.session_state.channel_configs = load_channel_config()

def get_video_files():
    """Get list of video files from current directory and videos folder"""
    video_extensions = ['.mp4', '.avi', '.mov', '.mkv', '.flv', '.wmv', '.webm']
    video_files = []
    
    try:
        # Check current directory
        for file in os.listdir('.'):
            if any(file.lower().endswith(ext) for ext in video_extensions):
                video_files.append(file)
        
        # Check videos folder
        if os.path.exists('videos'):
            for file in os.listdir('videos'):
                if any(file.lower().endswith(ext) for ext in video_extensions):
                    video_files.append(f"videos/{file}")
                    
    except Exception as e:
        st.error(f"Error reading video files: {e}")
    
    return sorted(video_files)

def run_ffmpeg(video_path, streaming_key, is_shorts=False, stream_index=None, quality='720p', broadcast_id=None, channel_name='default'):
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
            save_stream_config(st.session_state.streams)
        
        # Auto-start YouTube broadcast if broadcast_id is provided
        if broadcast_id:
            def start_broadcast_delayed():
                time.sleep(8)  # Wait for stream to establish
                success, message = start_youtube_broadcast(broadcast_id, channel_name)
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
                    save_stream_config(st.session_state.streams)
                    
                    # Auto-stop YouTube broadcast
                    if broadcast_id:
                        stop_youtube_broadcast(broadcast_id, channel_name)
                        
            except Exception as e:
                print(f"Error monitoring process: {e}")
        
        # Start monitoring in background thread
        threading.Thread(target=monitor_process, daemon=True).start()
        
        return True
        
    except Exception as e:
        st.error(f"Error starting stream: {e}")
        return False

def start_stream(video_path, streaming_key, is_shorts=False, stream_index=None, quality='720p', broadcast_id=None, channel_name='default'):
    """Start streaming with proper error handling"""
    if not os.path.exists(video_path):
        st.error(f"❌ Video file not found: {video_path}")
        return False
    
    return run_ffmpeg(video_path, streaming_key, is_shorts, stream_index, quality, broadcast_id, channel_name)

def stop_stream(stream_index):
    """Stop streaming process"""
    try:
        if stream_index in st.session_state.processes:
            process = st.session_state.processes[stream_index]
            
            # Get broadcast ID and channel for cleanup
            broadcast_id = st.session_state.streams.loc[stream_index, 'Broadcast ID']
            channel_name = st.session_state.streams.loc[stream_index, 'Channel']
            
            # Terminate FFmpeg process
            process.terminate()
            time.sleep(2)
            
            if process.poll() is None:
                process.kill()
            
            # Clean up
            del st.session_state.processes[stream_index]
            st.session_state.streams.loc[stream_index, 'Status'] = 'Dihentikan'
            st.session_state.streams.loc[stream_index, 'PID'] = 0
            save_stream_config(st.session_state.streams)
            
            # Stop YouTube broadcast
            if broadcast_id and broadcast_id != '':
                stop_youtube_broadcast(broadcast_id, channel_name)
            
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
                channel_name = row.get('Channel', 'default')
                if start_stream(row['Video'], row['Streaming Key'], row.get('Is Shorts', False), idx, quality, broadcast_id, channel_name):
                    st.session_state.streams.loc[idx, 'Jam Mulai'] = current_time
                    save_stream_config(st.session_state.streams)
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
                    channel_name = row.get('Channel', 'default')
                    if start_stream(row['Video'], row['Streaming Key'], row.get('Is Shorts', False), idx, quality, broadcast_id, channel_name):
                        st.session_state.streams.loc[idx, 'Jam Mulai'] = current_time
                        save_stream_config(st.session_state.streams)
                        
            except Exception as e:
                st.error(f"Error processing scheduled stream: {e}")

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

def export_config():
    """Export all configurations to a single JSON file"""
    try:
        config = {
            'streams': st.session_state.streams.to_dict('records'),
            'channels': get_available_channels(),
            'export_time': datetime.datetime.now().isoformat(),
            'version': '1.0'
        }
        
        config_json = json.dumps(config, indent=2)
        return config_json
    except Exception as e:
        st.error(f"Error exporting config: {e}")
        return None

def import_config(config_json):
    """Import configurations from JSON"""
    try:
        config = json.loads(config_json)
        
        # Import streams
        if 'streams' in config:
            streams_df = pd.DataFrame(config['streams'])
            st.session_state.streams = streams_df
            save_stream_config(streams_df)
        
        return True
    except Exception as e:
        st.error(f"Error importing config: {e}")
        return False

# Streamlit UI
st.set_page_config(page_title="🎬 Multi-Channel YouTube Live Stream Manager", layout="wide")

st.title("🎬 Multi-Channel YouTube Live Stream Manager")
st.markdown("---")

# Auto-refresh for scheduled streams
check_scheduled_streams()

# Main tabs
tab1, tab2, tab3, tab4 = st.tabs(["📺 Stream Manager", "🔧 Channel Management", "📊 Dashboard", "⚙️ Configuration"])

with tab2:
    st.header("🔧 Channel Management")
    
    # Upload credentials section
    st.subheader("📁 Upload Channel Credentials")
    
    col1, col2 = st.columns(2)
    
    with col1:
        with st.form("upload_credentials_form"):
            channel_name = st.text_input("📝 Channel Name", placeholder="e.g., main-channel, gaming-channel")
            uploaded_file = st.file_uploader("📤 Upload credentials.json", type=['json'])
            
            submit_credentials = st.form_submit_button("💾 Save Credentials")
            
            if submit_credentials and uploaded_file and channel_name:
                try:
                    # Save credentials file
                    credentials_path = get_channel_credentials_path(channel_name)
                    with open(credentials_path, 'wb') as f:
                        f.write(uploaded_file.getbuffer())
                    
                    st.success(f"✅ Credentials saved for channel '{channel_name}'")
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ Error saving credentials: {e}")
    
    with col2:
        # Available channels
        st.subheader("📋 Available Channels")
        available_channels = get_available_channels()
        
        if available_channels:
            for channel in available_channels:
                with st.container():
                    col_info, col_actions = st.columns([3, 1])
                    
                    with col_info:
                        st.write(f"**📺 {channel}**")
                        
                        # Get channel info
                        channel_info = get_channel_info(channel)
                        if channel_info:
                            st.caption(f"📊 {channel_info['title']}")
                            st.caption(f"👥 {channel_info['subscribers']} subscribers | 🎥 {channel_info['videos']} videos")
                        else:
                            st.caption("⚠️ Authentication required")
                    
                    with col_actions:
                        if st.button(f"🗑️ Remove", key=f"remove_{channel}"):
                            try:
                                # Remove credentials and token files
                                credentials_path = get_channel_credentials_path(channel)
                                token_path = get_channel_token_path(channel)
                                
                                if os.path.exists(credentials_path):
                                    os.remove(credentials_path)
                                if os.path.exists(token_path):
                                    os.remove(token_path)
                                
                                st.success(f"✅ Channel '{channel}' removed")
                                st.rerun()
                            except Exception as e:
                                st.error(f"❌ Error removing channel: {e}")
                    
                    st.markdown("---")
        else:
            st.info("📝 No channels configured. Upload credentials to get started!")

with tab1:
    # Sidebar for YouTube Broadcast Creation
    with st.sidebar:
        st.header("📺 Create YouTube Broadcast")
        
        # Channel selection
        available_channels = get_available_channels()
        if not available_channels:
            st.warning("⚠️ No channels available. Please configure channels first.")
        else:
            # Form for broadcast creation
            with st.form("broadcast_creation_form"):
                selected_channel = st.selectbox("📺 Select Channel", available_channels)
                
                # Show channel info
                if selected_channel:
                    channel_info = get_channel_info(selected_channel)
                    if channel_info:
                        st.info(f"📊 **{channel_info['title']}**\n👥 {channel_info['subscribers']} subscribers")
                
                title = st.text_input("🎬 Broadcast Title", value="Live Stream")
                description = st.text_area("📝 Description", value="Live streaming content")
                
                # Privacy settings
                privacy = st.selectbox("🔒 Privacy", ['public', 'unlisted', 'private'], index=0)
                
                # Time selection with Jakarta timezone
                jakarta_time = get_jakarta_time()
                current_time_str = format_jakarta_time(jakarta_time)
                
                st.write(f"🕐 Current Time: **{current_time_str}**")
                
                # Time selection options
                time_option = st.radio(
                    "⏰ Schedule Time",
                    ["🚀 Start NOW", "⏰ +5 minutes", "⏰ +15 minutes", "⏰ +30 minutes", "🕐 Custom time"]
                )
                
                # Custom time input (only show if custom is selected)
                custom_time = None
                if time_option == "🕐 Custom time":
                    custom_time = st.time_input("🕐 Set custom time", value=jakarta_time.time())
                
                # Submit button
                create_broadcast = st.form_submit_button("📺 Create Broadcast")
                
                # Process broadcast creation
                if create_broadcast and selected_channel:
                    with st.spinner(f"Creating YouTube broadcast on '{selected_channel}'..."):
                        # Determine broadcast time
                        if time_option == "🚀 Start NOW":
                            time_str = "NOW"
                        elif time_option == "⏰ +5 minutes":
                            future_time = jakarta_time + datetime.timedelta(minutes=5)
                            time_str = future_time.strftime('%H:%M')
                        elif time_option == "⏰ +15 minutes":
                            future_time = jakarta_time + datetime.timedelta(minutes=15)
                            time_str = future_time.strftime('%H:%M')
                        elif time_option == "⏰ +30 minutes":
                            future_time = jakarta_time + datetime.timedelta(minutes=30)
                            time_str = future_time.strftime('%H:%M')
                        elif time_option == "🕐 Custom time" and custom_time:
                            time_str = custom_time.strftime('%H:%M')
                        else:
                            time_str = "NOW"
                        
                        # Create broadcast
                        broadcast_id, stream_key, error = create_youtube_broadcast(
                            title, description, time_str, privacy, False, selected_channel
                        )
                        
                        if error:
                            st.error(f"❌ {error}")
                        else:
                            st.success(f"✅ Broadcast created successfully on '{selected_channel}'!")
                            st.info(f"🔑 Stream Key: `{stream_key}`")
                            st.info(f"🆔 Broadcast ID: `{broadcast_id}`")
                            
                            # Store broadcast info in session state for easy access
                            if 'last_broadcast' not in st.session_state:
                                st.session_state.last_broadcast = {}
                            st.session_state.last_broadcast = {
                                'broadcast_id': broadcast_id,
                                'stream_key': stream_key,
                                'channel': selected_channel,
                                'time_str': time_str
                            }

    # Main content area
    col1, col2 = st.columns([2, 1])

    with col1:
        st.header("📋 Stream Manager")
        
        # Quick add from last broadcast
        if 'last_broadcast' in st.session_state and st.session_state.last_broadcast:
            with st.expander("⚡ Quick Add from Last Broadcast", expanded=True):
                last_bc = st.session_state.last_broadcast
                st.info(f"📺 Channel: {last_bc['channel']} | 🔑 Key: {last_bc['stream_key'][:8]}****")
                
                with st.form("quick_add_form"):
                    video_files = get_video_files()
                    if video_files:
                        selected_video = st.selectbox("📹 Select Video", video_files)
                        quality = st.selectbox("🎥 Quality", ['240p', '360p', '480p', '720p', '1080p'], index=3)
                        is_shorts = st.checkbox("📱 YouTube Shorts format")
                        
                        quick_add_submit = st.form_submit_button("⚡ Add to Stream Manager")
                        
                        if quick_add_submit:
                            new_stream = pd.DataFrame({
                                'Video': [selected_video],
                                'Streaming Key': [last_bc['stream_key']],
                                'Jam Mulai': [last_bc['time_str']],
                                'Status': ['Menunggu'],
                                'PID': [0],
                                'Is Shorts': [is_shorts],
                                'Quality': [quality],
                                'Broadcast ID': [last_bc['broadcast_id']],
                                'Channel': [last_bc['channel']]
                            })
                            
                            st.session_state.streams = pd.concat([st.session_state.streams, new_stream], ignore_index=True)
                            save_stream_config(st.session_state.streams)
                            st.success("✅ Stream added to manager!")
                            st.rerun()
                    else:
                        st.warning("⚠️ No video files found.")
        
        # Add new stream form
        with st.expander("➕ Add New Stream", expanded=False):
            with st.form("add_stream_form"):
                video_files = get_video_files()
                available_channels = get_available_channels()
                
                if not video_files:
                    st.warning("⚠️ No video files found. Please add video files to the current directory or 'videos' folder.")
                    st.info("📁 Supported formats: .mp4, .avi, .mov, .mkv, .flv, .wmv, .webm")
                    st.stop()
                
                if not available_channels:
                    st.warning("⚠️ No channels available. Please configure channels first.")
                    st.stop()
                
                selected_video = st.selectbox("📹 Select Video", video_files)
                selected_channel = st.selectbox("📺 Select Channel", available_channels)
                streaming_key = st.text_input("🔑 Streaming Key", help="Your YouTube streaming key")
                
                # Time input with Jakarta timezone
                jakarta_time = get_jakarta_time()
                current_time_str = format_jakarta_time(jakarta_time)
                
                st.write(f"🕐 Current Time: **{current_time_str}**")
                
                # Time selection options
                stream_time_option = st.radio(
                    "⏰ Schedule Time",
                    ["🚀 Start NOW", "⏰ +5 minutes", "⏰ +15 minutes", "⏰ +30 minutes", "🕐 Custom time"],
                    key="stream_time_option"
                )
                
                # Custom time input
                stream_custom_time = None
                if stream_time_option == "🕐 Custom time":
                    stream_custom_time = st.time_input("🕐 Set custom time", value=jakarta_time.time(), key="stream_custom_time")
                
                quality = st.selectbox("🎥 Quality", ['240p', '360p', '480p', '720p', '1080p'], index=3)
                is_shorts = st.checkbox("📱 YouTube Shorts format")
                
                add_stream_submit = st.form_submit_button("📅 Add Stream")
                
                # Process stream addition
                if add_stream_submit and streaming_key and selected_channel:
                    # Determine schedule time
                    if stream_time_option == "🚀 Start NOW":
                        schedule_time = "NOW"
                    elif stream_time_option == "⏰ +5 minutes":
                        future_time = jakarta_time + datetime.timedelta(minutes=5)
                        schedule_time = format_jakarta_time(future_time)
                    elif stream_time_option == "⏰ +15 minutes":
                        future_time = jakarta_time + datetime.timedelta(minutes=15)
                        schedule_time = format_jakarta_time(future_time)
                    elif stream_time_option == "⏰ +30 minutes":
                        future_time = jakarta_time + datetime.timedelta(minutes=30)
                        schedule_time = format_jakarta_time(future_time)
                    elif stream_time_option == "🕐 Custom time" and stream_custom_time:
                        schedule_time = format_jakarta_time(
                            jakarta_time.replace(hour=stream_custom_time.hour, minute=stream_custom_time.minute, second=0, microsecond=0)
                        )
                    else:
                        schedule_time = "NOW"
                    
                    new_stream = pd.DataFrame({
                        'Video': [selected_video],
                        'Streaming Key': [streaming_key],
                        'Jam Mulai': [schedule_time],
                        'Status': ['Menunggu'],
                        'PID': [0],
                        'Is Shorts': [is_shorts],
                        'Quality': [quality],
                        'Broadcast ID': [''],
                        'Channel': [selected_channel]
                    })
                    
                    st.session_state.streams = pd.concat([st.session_state.streams, new_stream], ignore_index=True)
                    save_stream_config(st.session_state.streams)
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
                        st.caption(f"📺 Channel: {row.get('Channel', 'default')} | Quality: {row.get('Quality', '720p')}")
                        
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
                                channel_name = row.get('Channel', 'default')
                                if start_stream(row['Video'], row['Streaming Key'], row.get('Is Shorts', False), idx, quality, broadcast_id, channel_name):
                                    st.session_state.streams.loc[idx, 'Status'] = 'Sedang Live'
                                    st.session_state.streams.loc[idx, 'Jam Mulai'] = format_jakarta_time(get_jakarta_time())
                                    save_stream_config(st.session_state.streams)
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
                            save_stream_config(st.session_state.streams)
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
        
        # Channels count
        available_channels = get_available_channels()
        st.metric("📺 Available Channels", len(available_channels))
        
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

with tab3:
    st.header("📊 Multi-Channel Dashboard")
    
    available_channels = get_available_channels()
    
    if available_channels:
        # Channel overview
        st.subheader("📺 Channel Overview")
        
        for channel in available_channels:
            with st.expander(f"📺 {channel}", expanded=True):
                col1, col2, col3 = st.columns(3)
                
                # Get channel info
                channel_info = get_channel_info(channel)
                
                with col1:
                    if channel_info:
                        st.metric("📊 Channel", channel_info['title'])
                        st.metric("👥 Subscribers", channel_info['subscribers'])
                    else:
                        st.warning("⚠️ Authentication required")
                
                with col2:
                    # Active streams for this channel
                    channel_streams = st.session_state.streams[st.session_state.streams['Channel'] == channel]
                    active_count = len(channel_streams[channel_streams['Status'] == 'Sedang Live'])
                    waiting_count = len(channel_streams[channel_streams['Status'] == 'Menunggu'])
                    
                    st.metric("🟢 Active Streams", active_count)
                    st.metric("🟡 Waiting Streams", waiting_count)
                
                with col3:
                    if channel_info:
                        st.metric("🎥 Total Videos", channel_info['videos'])
                    
                    # Quick actions
                    if st.button(f"🔄 Refresh {channel}", key=f"refresh_{channel}"):
                        st.rerun()
        
        # Stream distribution chart
        if not st.session_state.streams.empty:
            st.subheader("📈 Stream Distribution by Channel")
            
            channel_counts = st.session_state.streams['Channel'].value_counts()
            st.bar_chart(channel_counts)
            
            # Status distribution
            st.subheader("📊 Stream Status Distribution")
            status_counts = st.session_state.streams['Status'].value_counts()
            st.bar_chart(status_counts)
    
    else:
        st.info("📝 No channels configured. Please add channels in the Channel Management tab.")

with tab4:
    st.header("⚙️ Configuration Management")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("📤 Export Configuration")
        st.write("Export all your streams and channel configurations to a JSON file.")
        
        if st.button("📤 Export Config"):
            config_json = export_config()
            if config_json:
                st.download_button(
                    label="💾 Download Configuration",
                    data=config_json,
                    file_name=f"stream_config_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                    mime="application/json"
                )
    
    with col2:
        st.subheader("📥 Import Configuration")
        st.write("Import stream configurations from a JSON file.")
        
        uploaded_config = st.file_uploader("📁 Upload Configuration File", type=['json'])
        
        if uploaded_config:
            if st.button("📥 Import Config"):
                try:
                    config_content = uploaded_config.read().decode('utf-8')
                    if import_config(config_content):
                        st.success("✅ Configuration imported successfully!")
                        st.rerun()
                    else:
                        st.error("❌ Failed to import configuration")
                except Exception as e:
                    st.error(f"❌ Error reading file: {e}")
    
    # Configuration files info
    st.subheader("📋 Configuration Files")
    
    config_files = []
    if os.path.exists('streams_config.json'):
        config_files.append("streams_config.json - Stream configurations")
    if os.path.exists('channel_config.json'):
        config_files.append("channel_config.json - Channel settings")
    
    for file_info in config_files:
        st.info(f"📄 {file_info}")
    
    if not config_files:
        st.info("📝 No configuration files found.")

# Footer
st.markdown("---")
st.markdown("🎬 **Multi-Channel YouTube Live Stream Manager** - Manage multiple YouTube channels with automated RTMP streaming")
