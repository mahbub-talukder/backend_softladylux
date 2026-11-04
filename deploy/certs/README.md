# TLS certificates

Put your certificate chain and private key here on the server, named exactly:

- `fullchain.pem`: your certificate followed by the intermediate chain
- `privkey.pem`: the private key (keep it private; the folder is git-ignored)

Any certificate authority or provider works. For a local or staging test only, create a
self-signed pair:

    openssl req -x509 -nodes -newkey rsa:2048 -days 30 -keyout privkey.pem -out fullchain.pem \
      -subj "/CN=localhost" -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"

Renew by replacing the two files and running `docker compose ... exec nginx nginx -s reload`.
If TLS is terminated by a provider's proxy or load balancer instead, use
`docker-compose.proxy-tls.yml` and ignore this folder (see docs/DEPLOYMENT.md).
