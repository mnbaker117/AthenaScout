#!/bin/bash
# Build the AthenaScout Docker image locally
#
# Usage:
#   ./build.sh              # builds athena-scout:latest
#   ./build.sh v12          # builds athena-scout:v12
#   ./build.sh --push       # builds and pushes to GHCR

TAG="${1:-latest}"
PUSH=false
if [ "$1" = "--push" ]; then
    TAG="latest"
    PUSH=true
fi

LOCAL_IMAGE="athena-scout:${TAG}"
GHCR_IMAGE="ghcr.io/mnbaker117/athenascout:${TAG}"

echo "Building ${LOCAL_IMAGE}..."
docker build -t "${LOCAL_IMAGE}" -t "${GHCR_IMAGE}" .

if [ $? -eq 0 ]; then
    echo ""
    echo "✓ Built ${LOCAL_IMAGE}"
    echo "  Also tagged as ${GHCR_IMAGE}"
    
    if [ "$PUSH" = true ]; then
        echo ""
        echo "Pushing to GHCR..."
        docker push "${GHCR_IMAGE}"
    fi
    
    echo ""
    echo "To run with docker-compose:"
    echo "  docker-compose up -d"
else
    echo ""
    echo "✗ Build failed"
    exit 1
fi
