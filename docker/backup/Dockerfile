FROM postgres:18-alpine
RUN apk add --no-cache rclone
# No config file: remotes come from BACKUP_REMOTE and RCLONE_* variables.
ENV RCLONE_CONFIG=/dev/null
COPY backup.sh /backup.sh
RUN chmod +x /backup.sh
ENTRYPOINT ["/backup.sh"]
