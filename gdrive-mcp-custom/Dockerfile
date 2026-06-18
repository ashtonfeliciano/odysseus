FROM node:20-slim
# Pre-install google-drive-mcp so it is cached in the image layer
RUN npx --yes google-drive-mcp@1.2.1 --version 2>/dev/null; exit 0
# Apply PKCE-stripping patch before the image is sealed
COPY patch.js /tmp/patch.js
RUN node /tmp/patch.js && rm /tmp/patch.js
