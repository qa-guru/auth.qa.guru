# Keycloak 26.7.3 built for postgres so prod can `start --optimized`.
# Passkeys are official since 26.4. Do not use start-dev on this image.
FROM quay.io/keycloak/keycloak:26.7.3

ENV KC_DB=postgres
ENV KC_HEALTH_ENABLED=true
ENV KC_HTTP_ENABLED=true

WORKDIR /opt/keycloak
RUN /opt/keycloak/bin/kc.sh build

ENTRYPOINT ["/opt/keycloak/bin/kc.sh"]
CMD ["start", "--optimized", "--import-realm"]
