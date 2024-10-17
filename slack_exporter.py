# slack_export.py

import os
import time
import argparse
import sys
import re
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from dotenv import load_dotenv
from datetime import datetime, timezone
from tqdm import tqdm  # Progress bar

# Load the environment variables from the .env file
load_dotenv()

# Get the Slack token from the environment variable
client = WebClient(token=os.getenv('SLACK_BOT_TOKEN'))

# Argument parsing
parser = argparse.ArgumentParser(description="Slack Message Exporter")
parser.add_argument('-n', '--channel_name_or_id', type=str, help="Specify the channel name or ID to fetch (optional)")
parser.add_argument('-t', '--output_type', type=str, choices=['txt', 'html'], default='txt',
                    help="Specify output type: txt or html (default is txt)")
args = parser.parse_args()

# Cache for user info to avoid multiple API calls for the same user
user_cache = {}

# Cache for messages to avoid redundant API calls for replies
message_cache = {}

# Link pattern for Slack's custom link format <http://example.com|example> and also <http://example.com>
link_pattern = re.compile(r'<(http[s]?://[^|>]+)(?:\|([^>]*))?>')


def get_user_info(user_id):
    """Fetches user information, using cache to reduce redundant API requests."""
    if user_id in user_cache:
        return user_cache[user_id]
    try:
        response = client.users_info(user=user_id)
        user = response['user']
        user_name = user.get('real_name') or user.get('display_name') or user.get('name')
        user_cache[user_id] = user_name
        return user_name
    except SlackApiError as e:
        return "Unknown User"


def get_workspace_name():
    """Fetches the workspace (team) name."""
    try:
        response = client.team_info()
        return response['team']['name']
    except SlackApiError as e:
        return "default_workspace"


def fetch_conversations():
    """Fetches a list of all public/private channels, DMs (IMs), and multi-party DMs (MPIMs)."""
    conversations = []
    try:
        # Fetch public and private channels
        response = client.conversations_list(types='public_channel,private_channel', limit=1000, exclude_archived=False)
        conversations.extend(response['channels'])

        # Fetch DMs (IMs)
        response = client.conversations_list(types='im', limit=1000)
        conversations.extend(response['channels'])

        # Fetch multi-party DMs (MPIMs)
        response = client.conversations_list(types='mpim', limit=1000)
        conversations.extend(response['channels'])

        return conversations
    except SlackApiError as e:
        print(f"Error fetching conversations: {e.response['error']}", file=sys.stderr)
        return []


def fetch_channel_by_id(channel_id):
    """Fetches channel information using the channel ID."""
    try:
        response = client.conversations_info(channel=channel_id)
        return response['channel']
    except SlackApiError as e:
        print(f"Error fetching channel info by ID: {e.response['error']}")
        return None


def fetch_channel_by_name(channel_name):
    """Fetches channel information using the channel name."""
    try:
        # Fetch public and private channels, MPIMs, and filter by name
        response = client.conversations_list(types='public_channel,private_channel,mpim', limit=1000)
        channels = response['channels']
        for channel in channels:
            if channel['name'] == channel_name:
                return channel
        print(f"Channel '{channel_name}' not found in conversations_list.")
        return None
    except SlackApiError as e:
        print(f"Error fetching channels: {e.response['error']}")
        return None


def fetch_channel_messages(channel_id, limit=1000):
    """Fetches the history of messages from a channel and filters out automatic messages."""
    try:
        result = client.conversations_history(channel=channel_id, limit=limit)
        messages = result['messages']

        # Filter out messages that have a subtype (automatic/system messages)
        user_messages = [msg for msg in messages if 'subtype' not in msg]

        return user_messages
    except SlackApiError as e:
        return []


def fetch_replies(channel_id, thread_ts):
    """Fetches replies for a specific thread in bulk and filters out automatic messages."""
    if thread_ts in message_cache:
        return message_cache[thread_ts]

    try:
        result = client.conversations_replies(channel=channel_id, ts=thread_ts)
        replies = result['messages'][1:]  # Skip the first message (the thread starter)

        # Filter out messages that have a subtype (automatic/system messages)
        user_replies = [reply for reply in replies if 'subtype' not in reply]

        message_cache[thread_ts] = user_replies
        return user_replies
    except SlackApiError as e:
        return []


def convert_ts_to_datetime(ts):
    """Converts Slack's timestamp format into a readable datetime format."""
    return datetime.fromtimestamp(float(ts), timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')


def parse_links(text, output_type='txt'):
    """Parse Slack's custom link format <http://example.com|example> and handle both <url|text> and <url>."""

    def replace_link(match):
        url = match.group(1)
        display_text = match.group(2) if match.group(2) else url  # Use URL if display text is empty
        if output_type == 'html':
            return f'<a href="{url}">{display_text}</a>'
        else:
            return f'{display_text} ({url})'

    return link_pattern.sub(replace_link, text)


def save_messages_to_txt(messages, conversation_name, conversation_id, workspace_folder, pbar):
    sanitized_conversation_name = "".join(
        [c for c in conversation_name if c.isalnum() or c in (' ', '-', '_')]).rstrip()

    # Ensure the workspace folder exists
    if not os.path.exists(workspace_folder):
        os.makedirs(workspace_folder)

    filename = os.path.join(workspace_folder, f'{sanitized_conversation_name}.txt')
    with open(filename, 'w', encoding='utf-8') as file:
        for message in messages:
            text = parse_links(message.get('text', ''), 'txt')
            timestamp = message.get('ts', '')
            user_id = message.get('user', '')
            user_name = get_user_info(user_id)
            formatted_time = convert_ts_to_datetime(timestamp)

            # Write the main message
            file.write(f"[{formatted_time}] {user_name}: {text}\n")

            # Check for replies and fetch them in bulk
            if 'thread_ts' in message:
                thread_ts = message['thread_ts']
                replies = fetch_replies(conversation_id, thread_ts)
                if replies:
                    for reply in replies:
                        reply_text = parse_links(reply.get('text', ''), 'txt')
                        reply_ts = reply.get('ts', '')
                        reply_user_id = reply.get('user', '')
                        reply_user_name = get_user_info(reply_user_id)
                        reply_time = convert_ts_to_datetime(reply_ts)

                        # Indent replies under the parent message
                        file.write(f"    [{reply_time}] {reply_user_name}: {reply_text}\n")
                    file.write("\n")  # Add an extra line break after the replies
            pbar.update(1)  # Update progress bar for each processed message
    tqdm.write(f"Messages saved to {filename}")


def save_messages_to_html(messages, conversation_name, conversation_id, workspace_folder, pbar):
    sanitized_conversation_name = "".join(
        [c for c in conversation_name if c.isalnum() or c in (' ', '-', '_')]).rstrip()

    # Ensure the workspace folder exists
    if not os.path.exists(workspace_folder):
        os.makedirs(workspace_folder)

    filename = os.path.join(workspace_folder, f'{sanitized_conversation_name}.html')
    with open(filename, 'w', encoding='utf-8') as file:
        file.write(f"<html><body><h1>Messages from {conversation_name}</h1><ul>\n")
        for message in messages:
            text = parse_links(message.get('text', ''), 'html')
            timestamp = message.get('ts', '')
            user_id = message.get('user', '')
            user_name = get_user_info(user_id)
            formatted_time = convert_ts_to_datetime(timestamp)

            # Write the main message
            file.write(f"<li><strong>[{formatted_time}] {user_name}:</strong> {text}</li>\n")

            # Check for replies and fetch them
            if 'thread_ts' in message:
                thread_ts = message['thread_ts']
                replies = fetch_replies(conversation_id, thread_ts)
                if replies:
                    file.write(f"<ul>\n")  # Indent replies under the parent message
                    for reply in replies:
                        reply_text = parse_links(reply.get('text', ''), 'html')
                        reply_ts = reply.get('ts', '')
                        reply_user_id = reply.get('user', '')
                        reply_user_name = get_user_info(reply_user_id)
                        reply_time = convert_ts_to_datetime(reply_ts)
                        file.write(f"<li><strong>[{reply_time}] {reply_user_name}:</strong> {reply_text}</li>\n")
                    file.write("</ul>\n")  # End the indentation for replies
            pbar.update(1)  # Update progress bar for each processed message
        file.write("</ul></body></html>")
    tqdm.write(f"Messages saved to {filename}")


def get_conversation_name(conversation):
    """Gets the name of the conversation, handles channels, DMs, and MPIMs."""
    if conversation['is_im']:  # Direct message
        user_id = conversation['user']
        user_name = get_user_info(user_id)
        return f"DM with {user_name}"
    elif conversation['is_mpim']:  # Multi-party direct message
        return conversation['name']  # MPIMs have a name
    else:  # Public or private channels
        return conversation['name']


def main():
    # Get the workspace name
    workspace_name = get_workspace_name()

    # Fetch all conversations (public channels, private channels, DMs, MPIMs)
    conversations = fetch_conversations()

    # Filter the conversations if a specific conversation name or ID is provided
    conversation = None
    if args.channel_name_or_id:
        if args.channel_name_or_id.startswith('C') or args.channel_name_or_id.startswith('D'):
            # Try to fetch by ID directly (works for channels and DMs)
            conversation = fetch_channel_by_id(args.channel_name_or_id)
        else:
            # Fetch by name (channels, MPIMs)
            conversation = fetch_channel_by_name(args.channel_name_or_id)
    else:
        print("No conversation name or ID provided.", file=sys.stderr)
        return

    if conversation:
        conversation_id = conversation['id']
        conversation_name = get_conversation_name(conversation)

        # Print progress for conversation processing
        tqdm.write(f"Processing {conversation_name} (ID: {conversation_id})", file=sys.stderr)

        # Fetch messages from the conversation
        messages = fetch_channel_messages(conversation_id)

        # Reverse the message order to go from oldest to newest
        messages = list(reversed(messages))

        # Create a folder for the workspace
        workspace_folder = workspace_name.replace(' ', '_')

        # Use a progress bar to indicate how many messages have been processed
        with tqdm(total=len(messages), desc=f"Processing {conversation_name}") as pbar:
            # Save the messages to a text file or HTML file based on user selection
            if messages:
                if args.output_type == 'txt':
                    save_messages_to_txt(messages, conversation_name, conversation_id, workspace_folder, pbar)
                elif args.output_type == 'html':
                    save_messages_to_html(messages, conversation_name, conversation_id, workspace_folder, pbar)
            else:
                print(f"No messages found for {conversation_name} or an error occurred.", file=sys.stderr)
    else:
        print("No valid conversation found.", file=sys.stderr)


if __name__ == "__main__":
    main()
