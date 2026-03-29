#!/bin/bash

# 1. check github version
# REPO = "https://github.com/kxnjo/recycling-bin/main"
# VERSION_FILE = "version.txt"
# MAIN_FILE = "main_nomqtt.py"

# # git method
# git fetch origin
# LOCAL = $(git rev-parse HEAD)
# remote = $(git rev-parse origin/main)

# # 1a. get local version
# if [ -f "$VERSION_FILE" ] ; then
#     LOCAL_VERSION = $( cat "$VERSION_FILE" | tr -d ' \n' )
# else # if not found, use a default version
#     LOCAL_VERSION = "0.0.0"
# fi

# echo "Local Version: $LOCAL_VERSION"

# # 1b. get remote version
# REMOTE_VERSION = $(curl -s "$REPO/version.txt" | tr -d ' \n')

# if [ -z "$REMOTE_VERSION" ] ; then
#     echo "Failed to fetch remote version"
#     exit 1
# fi

# echo "Local Version: $REMOTE_VERSION"

# # 1c. check if version unmatch
# if [ "$REMOTE_VERSION" != "$LOCAL_VERSION" ] ; then
#     echo "New update found!! Downloading..."

#     # 1d. download updated file
#     # curl -s "$BASE_URL/$MAIL_FILE" -o "$MAIN_FILE" # only update 1 file for now,, trytry
#     git pull origin main

#     # 1e. update version fil
#     echo "$REMOTE_VERSION" > "$VERSION_FILE"

#     echo "Update done!"
# else
#     echo "Already updated to the latest version"
# fi

# send ping to endpoint

# 2. activate .venv
set -e
cd /home/dougey/Desktop/recycling-bin

exec /home/dougey/Desktop/recycling-bin/.venv/bin/python /home/dougey/Desktop/recycling-bin/main.py

# to update .sh, run the following
# 1. chmod +x boot.sh
# 2. nano /etc/systemd/system/smartbin-boot.service
# 3. Update ExecStart to ExecStart=/home/dougey/Desktop/recycling-bin/boot.sh
# 4. update the service file.