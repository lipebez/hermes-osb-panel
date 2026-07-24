# Hermes OSB Panel — v0.2

## Objetivo

Ampliar o painel do Open Second Brain para mostrar **memória operacional** e **vault completo do Obsidian** como duas camadas visuais complementares, usando a estrutura real do disco.

## Entregáveis

1. Plano versionado atualizado.
2. Backend v0.2 com:
   - contadores separados para Brain e Vault;
   - grafo com nós de diretórios do vault completo;
   - seção de artefatos com notas do Obsidian fora do Brain.
3. Frontend v0.2 com:
   - novos KPIs separados (`Brain` e `Vault`);
   - nova seção visual `Memórias do Obsidian`;
   - grafo maior e mais vivo.
4. Testes v0.2 cobrindo:
   - nós/contadores do Brain;
   - nós/contadores do Vault;
   - front/manifest indicando as seções novas.
5. Plugin instalado e verificado:
   - `hermes plugins list --plain --no-bundled`
   - `/api/dashboard/plugins`
   - assets e rota do dashboard
   - snapshot autenticado
6. Milestone no Brain.

## Princípios

- Continuar **read-only**.
- Separar explicitamente:
  - `Brain` = memória operacional do OSB;
  - `Vault` = notes do Obsidian como mapa completo.
- Não mesclar conceitos.
- Não inventar nós artificiais.
- Resolver via `FilesystemFirstStrategy` para leitura local.

## Estratégia de nós

### 1) Brain nodes

Contagem por arquivo real sob `Brain/**/*.md`.

IDs:
- `Brain/active.md`
- `Brain/_BRAIN.md`
- `Brain/log/YYYY-MM-DD.*.md`
- futuros `preferences`, `inbox`, `retired`

### 2) Vault nodes

Contagem por nota real do Obsidian fora de `Brain/`.

IDs relativos ao vault, ex.:
- `02 Projetos/Hermes - Mapa operacional.md`
- `04 Runbooks/WhatsApp Bridge - Envio e mídia.md`

### 3) Áreas visuais

Cada nota de vault mapeia para uma área estável:
- `inbox`
- `projects`
- `clients`
- `runbooks`
- `decisions`
- `references`
- `templates`
- `other`

Edges possíveis:
- `vault-note -> area`
- `brain-log -> brain-active`
- wikilink quando existir

## Frontend

### KPIs

Mantém 4 cards, mas muda labels/valores:
1. `Preferências`
2. `Sinais`
3. `Logs`
4. `Notas`

Com seção secundária:
- `Brain nodes`
- `Vault notes`

### Seções

1. Hero / provider status
2. KPI cards
3. `PONTOS DAS MEMÓRIAS` (Brain + Vault)
4. `MEMÓRIAS DO OBSIDIAN`
5. Timeline do Brain
6. Artefatos do Brain

## Verificação

Na cópia-fonte e na cópia instalada:
- unittest
- AST parse sem bytecode
- node --check
- snapshot JSON
- dashboard `/second-brain`
- assets estáticos servidos
- screenshot/headless se possível
- brain note milestone

## Decisões

- v0.1 permanece compatível.
- v0.2 prioriza riqueza visual sem ações perigosas.
- A seção mais importante agora é a nova aba/grid de notas do Obsidian.
