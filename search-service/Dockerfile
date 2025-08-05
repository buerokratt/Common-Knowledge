FROM node:18-alpine

# Set working directory
WORKDIR /app

# Copy package files first for better caching
COPY package*.json ./

# Install dependencies (use npm install instead of npm ci)
RUN npm install --only=production && npm cache clean --force

# Create data directory
RUN mkdir -p /app/data

# Copy application code
COPY . .

# Create non-root user for security
RUN addgroup -g 1001 -S nodejs && \
    adduser -S searchuser -u 1001 -G nodejs

# Set proper permissions
RUN chown -R searchuser:nodejs /app

# Switch to non-root user
USER searchuser

# Expose port
EXPOSE 3000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD node -e "require('http').get('http://localhost:3000/health', (res) => process.exit(res.statusCode === 200 ? 0 : 1)).on('error', () => process.exit(1))"

# Start the application
CMD ["node", "index.js"]