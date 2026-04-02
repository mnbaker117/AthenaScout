#!/bin/bash
# Build the AthenaScout Docker image locally
# Run this from the project root directory after extracting the tar
#
# Usage:
#   ./build.sh              # builds athena-scout:latest
#   ./build.sh v11          # builds athena-scout:v11

TAG="${1:-latest}"
IMAGE="athena-scout:${TAG}"

echo "Building ${IMAGE}..."
docker build -t "${IMAGE}" .

if [ $? -eq 0 ]; then
    echo ""
    echo "✓ Built ${IMAGE} successfully"
    echo ""
    echo "To run with docker-compose:"
    echo "  docker-compose up -d"
    echo ""
    echo "To run standalone:"
    echo "  docker run -d --name athena-scout \\"
    echo "    -p 8787:8787 \\"
    echo "    -v /path/to/calibre/library:/calibre:ro \\"
    echo "    -v ./data:/app/data \\"
    echo "    ${IMAGE}"
else
    echo ""
    echo "✗ Build failed"
    exit 1
fi
