# Pharma ERP Enterprise v5.8.7.1

## Production Route & Inventory Hotfix

- Restored the missing inventory page route.
- Restored product activate/deactivate route.
- Restored stock transfer list, detail, send, receive, and cancel routes.
- Added inventory and stock-transfer permissions to the permission catalog.
- Added stricter validation for locations, products, duplicate transfer items, negative costs, and received quantities.
- Made stock send/receive operations transactional to prevent partial balance updates.
- Preserved the v5.8.7 database and existing features without architectural changes.
