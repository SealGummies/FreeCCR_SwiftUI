#!/bin/bash

# Deployment script for the activation server on Fly.io

set -e

echo "🚀 Deploying activation server to Fly.io..."

# Check if fly CLI is installed
if ! command -v fly &> /dev/null; then
    echo "❌ Fly CLI is not installed. Please install it first:"
    echo "   https://fly.io/docs/hands-on/install-flyctl/"
    exit 1
fi

# Check if logged in to Fly.io
if ! fly auth whoami &> /dev/null; then
    echo "❌ You are not logged in to Fly.io. Please run 'fly auth login' first."
    exit 1
fi

# Build and deploy
echo "📦 Building and deploying..."
fly deploy

# Check deployment status
echo "✅ Deployment complete!"
echo "🌐 Your activation server is available at: https://$(fly info --name activation-svr-src --json | jq -r '.Hostname')"

# Optional: Set secrets (uncomment and modify as needed)
# echo "🔐 Setting secrets..."
# fly secrets set LEMON_SQUEEZY_API_KEY=your-api-key-here

echo "📋 Next steps:"
echo "1. Update your Python client to use the new host"
echo "2. Test the endpoints with your actual license keys"
echo "3. Monitor logs with: fly logs"
