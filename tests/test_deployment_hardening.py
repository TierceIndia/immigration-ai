"""Static guards on the shipped container/deployment configuration.

Docker is not required for these tests; they catch the regressions that are
cheap to reintroduce and expensive to notice in production: running as root,
 publishing the database/API directly, or shipping a production compose file
that silently falls back to development secrets.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _compose() -> dict:
    return yaml.safe_load(_read("docker-compose.prod.yml"))


class TestApiImage:
    def test_runs_as_a_fixed_unprivileged_user(self):
        dockerfile = _read("Dockerfile")
        assert "groupadd --gid 10001 app" in dockerfile
        assert "useradd --uid 10001" in dockerfile
        lines = dockerfile.splitlines()
        assert "USER app" in lines
        assert not any(line.strip() == "USER root" for line in lines)
        user_index = lines.index("USER app")
        app_dir_index = max(
            i for i, line in enumerate(lines) if line.startswith("RUN mkdir -p /app/raw")
        )
        assert user_index > app_dir_index

    def test_declares_database_readiness_without_shelling_out_to_the_host(self):
        dockerfile = _read("Dockerfile")
        assert "HEALTHCHECK" in dockerfile
        assert "http://127.0.0.1:8000/ready" in dockerfile


class TestAdminUiImage:
    def test_uses_unprivileged_nginx_on_a_non_privileged_port(self):
        dockerfile = _read("frontend/Dockerfile")
        assert "nginxinc/nginx-unprivileged" in dockerfile
        assert not re.search(r"^USER\s+root\s*$", dockerfile, re.M)
        assert "EXPOSE 8080" in dockerfile
        assert "listen 8080;" in _read("frontend/nginx.conf")

    def test_proxies_the_forwarded_tls_scheme_instead_of_overwriting_it(self):
        nginx = _read("frontend/nginx.conf")
        assert "map $http_x_forwarded_proto $forwarded_proto" in nginx
        assert "proxy_set_header X-Forwarded-Proto $forwarded_proto;" in nginx
        assert "proxy_set_header X-Forwarded-Proto $scheme;" not in nginx

    def test_has_a_container_local_liveness_probe(self):
        assert "location = /healthz" in _read("frontend/nginx.conf")
        assert "/healthz" in _read("frontend/Dockerfile")


class TestProductionCompose:
    def test_does_not_publish_the_database_or_api(self):
        services = _compose()["services"]
        assert "ports" not in services["postgres"]
        assert "ports" not in services["api"]
        assert "ports" not in services["frontend"]
        assert set(services["caddy"]["ports"]) == {"80:80", "443:443", "443:443/udp"}

    def test_keeps_database_and_ui_networks_without_egress(self):
        networks = _compose()["networks"]
        assert networks["db"]["internal"] is True
        assert networks["app"]["internal"] is True
        api_networks = _compose()["services"]["api"]["networks"]
        assert "egress" in api_networks
        assert "db" in api_networks
        assert "app" in api_networks

    def test_required_values_have_no_development_fallback(self):
        raw = _read("docker-compose.prod.yml")
        for key in (
            "DOMAIN",
            "POSTGRES_PASSWORD",
            "DATABASE_URL",
            "JWT_SECRET_KEY",
            "ADMIN_EMAIL",
            "ADMIN_PASSWORD",
        ):
            assert "${" + key + ":?" in raw, f"{key} must fail closed when unset"

    def test_api_runs_in_production_mode_with_fetch_guards_on(self):
        env = _compose()["services"]["api"]["environment"]
        assert env["ENVIRONMENT"] == "production"
        assert env["DEBUG"] == "false"
        assert env["ALLOW_PUBLIC_REGISTRATION"] == "false"
        assert env["PASSWORD_MIN_LENGTH"] == "8"
        assert env["FETCH_ALLOW_PRIVATE_HOSTS"] == "false"
        assert env["FETCH_VALIDATE_DESTINATION_DNS"] == "true"
        assert env["CORS_ORIGINS"].startswith("https://")

    def test_refuses_default_database_password(self):
        # production_config.validate_database_url rejects well-known dev passwords
        from production_config import validate_database_url
        problems = validate_database_url("postgresql+psycopg2://user:immigration_dev@host/db")
        assert any("well-known development database password" in p for p in problems)
        problems = validate_database_url("postgresql+psycopg2://user:postgres@host/db")
        assert any("well-known development database password" in p for p in problems)
        problems = validate_database_url("postgresql+psycopg2://user:password@host/db")
        assert any("well-known development database password" in p for p in problems)

    def test_containers_drop_privilege_escalation(self):
        services = _compose()["services"]
        for name in ("postgres", "api", "frontend", "caddy"):
            assert "no-new-privileges:true" in services[name]["security_opt"]

    def test_containers_drop_all_capabilities(self):
        services = _compose()["services"]
        for name in ("postgres", "api", "frontend", "caddy"):
            assert services[name].get("cap_drop") == ["ALL"], f"{name} must drop all capabilities"

    def test_containers_use_read_only_rootfs_with_tmpfs(self):
        services = _compose()["services"]
        for name in ("postgres", "api", "frontend", "caddy"):
            assert services[name].get("read_only") is True, f"{name} must have read_only rootfs"
            assert "tmpfs" in services[name], f"{name} must define tmpfs for writable directories"


class TestBuildContexts:
    def test_api_context_keeps_files_the_image_copies(self):
        ignored = {
            line.strip().rstrip("/")
            for line in _read(".dockerignore").splitlines()
            if line.strip() and not line.strip().startswith(("#", "!"))
        }
        assert "schema" not in ignored
        assert "src" not in ignored
        assert "migrations" not in ignored
        assert ".env" in ignored

    def test_frontend_context_excludes_build_and_test_outputs(self):
        ignored = set(_read("frontend/.dockerignore").splitlines())
        assert {"node_modules", "dist", "test-results", "playwright-report"} <= ignored


class TestProductionEnvExample:
    def test_sensitive_values_are_not_prefilled(self):
        example = _read("deploy/prod.env.example")
        for key in (
            "DOMAIN",
            "POSTGRES_PASSWORD",
            "DATABASE_URL",
            "JWT_SECRET_KEY",
            "ADMIN_EMAIL",
            "ADMIN_PASSWORD",
        ):
            assert not re.search(rf"^{key}=\S+", example, re.M), (
                f"{key} must stay blank/commented in the example"
            )
