FROM golang:1.21.7-alpine AS builder

WORKDIR /app

COPY go.mod go.sum ./
RUN go mod download

COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -o main .

FROM alpine:latest

WORKDIR /app

COPY --from=builder /app/main .

EXPOSE ${PORT:-8080}

# Set default values for environment variables
ENV BACKEND_IPS=""
ENV MAX_REQUESTS_PER_BACKEND=""
ENV REDIS_URL=""
ENV PORT=8080
ENV GIN_MODE=release
ENV ALLOW_ORIGIN="*"

CMD ["./main"]