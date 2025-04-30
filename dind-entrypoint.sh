#!/bin/sh
set -e

# Start Docker daemon in the background (as root)
# Use --storage-driver=vfs for better compatibility inside Docker, unless overlay2 is known to work
dockerd --host=unix:///var/run/docker.sock --storage-driver=vfs &

# Wait a bit for Docker daemon to start (adjust sleep time if needed)
sleep 5

# Check if Docker is running
docker info > /dev/null 2>&1
if [ $? -ne 0 ]; then
  echo "Docker daemon failed to start." >&2
  exit 1
fi

echo "Docker daemon started successfully inside the container."

# Execute the command passed into the container AS pod-user
exec gosu pod-user "$@" 