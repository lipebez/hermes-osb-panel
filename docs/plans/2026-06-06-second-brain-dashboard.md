# Second Brain Dashboard Companion — plano de implementação

## Objetivo

Criar um plugin visual separado chamado `hermes-osb-panel`, exibindo o Open Second Brain configurado como uma aba premium no dashboard, sem modificar o plugin upstream `open-second-brain`.

## Escopo v1

- Dashboard extension em `/second-brain`.
- Plugin runtime mínimo, read-only.
- Backend FastAPI em `dashboard/plugin_api.py`, montado em `/api/plugins/hermes-osb-panel/`.
- Snapshot vivo do vault OSB configurado pelo ambiente local.
- Cards de status: provider, vault, active.md, inbox, preferences, retired, logs, semântica.
- Preview de `Brain/active.md`.
- Timeline dos logs recentes.
- Tela visual “pontos das memórias tipo Obsidian”: grafo SVG/React com nós por artefato do Brain e conexões por wikilinks/estrutura.
- Design dark premium compacto inspirado em Linear/Sentry, com “papel digital”/Obsidian nos nós.

## Segurança

- Não editar o OSB upstream.
- Não criar novo sistema de memória paralelo.
- Não expor segredos.
- Endpoints v1 read-only.
- Truncar previews para proteger token/UI.
- Lidar com diretórios ausentes sem erro.

## Arquitetura

```text
project-root/
  plugin.yaml
  __init__.py
  README.md
  pyproject.toml
  dashboard/
    manifest.json
    plugin_api.py
    dist/
      index.js
      style.css
  tests/
    test_plugin_api.py
    test_dashboard_assets.py
```

## Contratos de aceitação

1. `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -v` passa.
2. `PYTHONDONTWRITEBYTECODE=1 python3 -B -c 'import ast, pathlib; [ast.parse(pathlib.Path(path).read_text(encoding="utf-8"), filename=path) for path in ("__init__.py", "dashboard/plugin_api.py")]'` passa sem gerar bytecode.
3. `node --check dashboard/dist/index.js` passa.
4. `hermes plugins list --plain --no-bundled` mostra `hermes-osb-panel enabled`.
5. `/api/dashboard/plugins` inclui `hermes-osb-panel` com path `/second-brain`.
6. Assets retornam 200:
   - `/dashboard-plugins/hermes-osb-panel/dist/index.js`
   - `/dashboard-plugins/hermes-osb-panel/dist/style.css`
7. API viva retorna 200 autenticada pela própria dashboard sessão quando chamada via UI; local sem token pode retornar 401, esperado.
8. A rota SPA `/second-brain` retorna 200.

## Fora do escopo v1

- Rodar `dream` pelo painel.
- Pin/reject/merge/edit de preferências.
- Reindex, rollback ou ações destrutivas.
- Importar memórias externas.

## Próxima fase sugerida

Adicionar recall debugger e ações de governança com confirmação + snapshot/rollback visível.
