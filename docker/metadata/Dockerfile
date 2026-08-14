FROM postgres:17-alpine

COPY --chmod=0555 secret-entrypoint.sh /usr/local/bin/schemii-metadata-postgres
ENTRYPOINT ["/usr/local/bin/schemii-metadata-postgres"]
CMD ["postgres"]
