# Nickname Guardian Discord Bot

## Overview
Nickname Guardian is a Discord bot that protects server members from unauthorized nickname changes. The bot automatically reverts nickname changes for users who don't have immune roles, providing server administrators with control over who can change their own nicknames.

## Features
- **Nickname Protection**: Automatically reverts nickname changes for non-immune users
- **Immune Roles**: Configure roles that are exempt from nickname enforcement
- **Firebase Integration**: Uses Google Cloud Firestore to store server-specific immune role configurations
- **Discord Commands**:
  - `!immune_role <role>`: Add a role to the immune list
  - `!immune_roles`: List all immune roles for the server
  - `!test_firebase`: Test Firebase connection
  - `!bot_status`: Check bot and Firebase status

## Technology Stack
- **Language**: Python 3.11
- **Framework**: discord.py 2.6+
- **Database**: Google Cloud Firestore (Firebase)
- **Keep-Alive**: Flask web server on port 8080

## Setup Requirements
1. **DISCORD_TOKEN**: Your Discord bot token from Discord Developer Portal
2. **FIREBASE_CONFIG**: Firebase service account JSON configuration

## Project Structure
- `main.py`: Main bot application with all bot logic
- `requirements.txt`: Python dependencies
- `replit.nix`: Nix configuration for Python environment
- `.gitignore`: Git ignore patterns for Python projects

## Database Structure
Firebase Firestore collections:
- `servers/{guild_id}/immune_roles/{role_id}`: Stores immune roles per server
  - `role_id`: Discord role ID
  - `role_name`: Role name
  - `guild_id`: Discord server ID
  - `added_at`: Timestamp when role was added

## Recent Changes
- 2025-11-01: Initial project setup with Python 3.11 and dependencies installed
- 2025-11-01: Fixed Firebase Firestore client initialization to properly use project_id
- 2025-11-01: Bot successfully connected to Discord and Firebase - fully operational

## Current Status
- Bot is running and connected to Discord
- Firebase/Firestore integration is working
- Connected to 1 Discord server
- All features operational

## User Preferences
None configured yet.
