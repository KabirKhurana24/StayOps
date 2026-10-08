# Convenience targets for POSIX shells. Every target delegates to a pnpm script,
# so Windows users get the same commands via `pnpm <script>`.
.PHONY: setup dev-api dev-web migrate seed test test-api test-e2e check worker

setup:     ; pnpm bootstrap
dev-api:   ; pnpm dev:api
dev-web:   ; pnpm dev:web
migrate:   ; pnpm db:migrate
seed:      ; pnpm db:seed
test:      ; pnpm test
test-api:  ; pnpm test:api
test-e2e:  ; pnpm test:e2e
check:     ; pnpm check
worker:    ; node scripts/api.mjs worker --loop 5
