# IMAN

IMAN discovers Spanish public procurement opportunities and EU funding calls, analyses them, and helps CTIC decide which ones to pursue.

## Language

### Spanish public procurement

**Tender**:
A public procurement opportunity published by a Spanish contracting authority, identified by its feed entry id.
_Avoid_: Licitación (in code and docs), contract, expediente

**Tender Status**:
The official procurement stage of a Tender as declared by the contracting authority in the feed: pre-announcement, published, under evaluation, awarded, resolved, or cancelled.
_Avoid_: State, phase, ContractFolderStatusCode (outside parsing code)

**Tracked Tender** (*en seguimiento*):
A Tender that a person has chosen to follow through its official lifecycle; tracking ends when the person stops it, the Tender reaches a terminal Tender Status (resolved or cancelled), or the feed withdraws it.
_Avoid_: Favourite, bookmarked, interested, watched

**Tender Change**:
A recorded difference between two successive feed versions of a Tracked Tender — a Tender Status transition or any amended detail (deadline, amounts, documents).
_Avoid_: Update, event, revision

**Lot** (*lote*):
An independently awardable part of a Tender; a Tender not split into lots is treated as having a single Lot.
_Avoid_: Package, part, batch

**Award** (*adjudicación*):
The outcome of a single Lot: the winning company, the award amount and the number of bids received — or that the Lot was declared void (*desierto*).
_Avoid_: Result, resolution
