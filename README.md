# Prerelease Night

Private Magic prerelease-style sealed events for a friend group.

## Current slice: v0.1

- Host an event and share a five-character invite code
- Friends join a live lobby
- Server generates each player exactly one private prerelease kit
- Six simulated boosters plus one foil rare/mythic promo
- Open packs individually
- Build a 40+ card sealed deck
- Unlimited Plains, Island, Swamp, Mountain and Forest
- Player pools are authenticated and private
- Events persist to `data/events` so restarting the server does not reroll packs
- Rules-engine adapter boundary is already in place for the future XMage bridge

Real booster generation uses the official `mtgjson-sdk` booster simulator rather than hand-written rarity odds.

## Windows setup

From PowerShell in the project folder:

```powershell
.\scripts\setup.ps1
.\scripts\start.ps1
```

Then open:

```text
http://localhost:8000
```

The first MTGJSON-backed run may need to download/synchronize its local data store.

### UI-only smoke test

To test the entire multiplayer/prerelease flow without waiting for MTGJSON data:

```powershell
.\scripts\start-demo.ps1
```

Then use two browser windows. Create an event in one and join the code from the other.

## Current real-set default

The UI defaults to `FRA` (Reality Fracture). You can enter any set code that the MTGJSON SDK reports with booster configurations.

## Architecture

```text
Browser UI
   |
FastAPI event server
   |-- lobby + private player sessions
   |-- persisted prerelease kits
   |-- deck builder state
   |
MTGJSON provider
   |-- official booster simulation
   |-- card metadata / Scryfall IDs
   |
RulesEngineAdapter
   `-- XMage bridge (next major gameplay phase)
```

## Important implementation note about promos

v0.1 models the prerelease promo as a foil rare/mythic from the selected main set. That matches the common prerelease product shape, but some sets have exclusions, seeded promos, character boosters or other special configurations. The promo selector is isolated behind the provider so we can add exact per-product eligibility without touching the event/deck code.

## Next build targets

1. Exact prerelease-product/promo modeling from MTGJSON sealed-product data
2. Swiss pairings and best-of-three match state
3. Sideboarding between games
4. XMage bridge proof: two browser players completing one rules-enforced turn
5. Private internet hosting flow for friends outside the home network
