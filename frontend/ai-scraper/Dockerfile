# Stage 1: Build the React application
FROM node:22-alpine AS builder

WORKDIR /app

# Copy dependency files first for better Docker layer caching
COPY package*.json ./

RUN npm ci

# Copy the rest of the application
COPY . .

# Build production bundle
RUN npm run build


# Stage 2: Serve with Nginx
FROM nginx:alpine

# Remove default Nginx files
RUN rm -rf /usr/share/nginx/html/*

# Copy Vite's production build
COPY --from=builder /app/dist /usr/share/nginx/html

# Expose HTTP
EXPOSE 80

# Start Nginx
CMD ["nginx", "-g", "daemon off;"]