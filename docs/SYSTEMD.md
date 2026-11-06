# Standalone systemd runtime

The standalone repository owns `deploy/systemd/boursamind-rag.service`. The
single-host template uses checkout `/www/wwwroot/BoursaMind-RAG`, virtual
environment `.venv`, protected environment `/etc/boursamind/rag.env`, and
loopback listener `127.0.0.1:8002`.

Port `8002` is the established systemd bind because the platform API may use
`8001` on the host. The container image always listens on `8001` internally.
Production containers sharing `boursamind-internal` use
`http://boursamind-rag:8001`; a platform systemd process uses
`http://127.0.0.1:8002`.

```bash
python -m app.production_preflight
sudo systemd-analyze verify deploy/systemd/boursamind-rag.service
sudo install -o root -g root -m 0644 deploy/systemd/boursamind-rag.service \
  /etc/systemd/system/boursamind-rag.service
sudo systemctl daemon-reload
sudo systemctl enable --now boursamind-rag
curl --fail http://127.0.0.1:8002/v1/health/ready
```

The service preserves `ProtectHome=true` and the existing hardening. Its
database, migration head and rollback remain independent from the platform.
