#!/bin/bash
# Generates a self-signed SSL certificate for local/home server use
# For production use Let's Encrypt (certbot) instead

mkdir -p nginx/certs

openssl req -x509 -nodes -days 365 \
  -newkey rsa:2048 \
  -keyout nginx/certs/key.pem \
  -out nginx/certs/cert.pem \
  -subj "/C=US/ST=Local/L=Local/O=HomeServer/CN=localhost"

echo ""
echo "✅ Certificates generated in nginx/certs/"
echo "   cert.pem + key.pem"
echo ""
echo "Note: Browser will show a warning for self-signed certs — that's normal."
echo "For production, use: certbot certonly --standalone -d yourdomain.com"
