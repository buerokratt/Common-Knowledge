FROM node:22.0.0-alpine AS image
WORKDIR /app
COPY ./package.json .

FROM image AS build
RUN npm install --legacy-peer-deps --mode=development --ignore-scripts
COPY . .
RUN ./node_modules/.bin/vite build --mode=development
VOLUME /app

ENV NODE_ENV development
ENV PORT=3001
ENV REACT_APP_ENABLE_HIDDEN_FEATURES TRUE

EXPOSE 3001

CMD ["npm", "run", "dev", "--", "--host", "--port", "${PORT}"]

FROM nginx:1.26.0-alpine AS web
COPY ./nginx/http-nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build ./app/dist/assets /usr/share/nginx/html/ckb/assets
COPY --from=build ./app/dist/index.html /usr/share/nginx/html/ckb
RUN apk add --no-cache bash
COPY ./nginx/scripts/env.sh /docker-entrypoint.d/env.sh
RUN chmod +x /docker-entrypoint.d/env.sh
EXPOSE 3001

ENTRYPOINT [ "bash", "/docker-entrypoint.d/env.sh" ]
