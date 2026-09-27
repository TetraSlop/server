# Original catalogue availability and explicit offers

The standard shop now uses one immutable offer snapshot for each session.
Init SP/SN/CMP/HD and Buy validation agree with the original client catalogue.
Default offers remain unchanged. `--shop-offers shop_offers.example.json`
explicitly enables a personalized pack and the two FFX packs; this is host
configuration, not evidence of historical eligibility or reward schedules.
Restart requires the chosen configuration again. Account saves do not silently
reenable offers, and changing configuration cannot alter a cached live session.

Original Init10105201..101054B9 sets pack5 category from SP, copies/renames it
from hex SN, sets listed HD indexes to category1 when CMP is nonzero or4 when
zero, and maps guild categories7..9. Guild0 enables all three; guild1..3 enable
only the corresponding pack. HD is bounded to the actual78 catalogue indexes.
Hidden CPU/Prize Center records remain hidden unless a trusted configuration
explicitly overrides them; there is no client-controlled entitlement path.

The original visible-list loop101190F7..101191E8 includes flags1/3. Flag2
(normally Pauper's Pack) additionally requires both fewer than5 cards AND
money below the Beginner's Pack price1000. The server previously checked only
the card count; it now enforces both. Buy still checks the selected original
asset price, and purchase debit/cards commit only on Get.

SN lengths above7 follow an unusual original truncation (`length-7` bytes
retained through16). A visible personalized pack therefore requires1..7
printable bytes in configuration. This preserves the configured visible name
exactly rather than silently truncating it. Empty SN is retained only for the
default hidden special pack.

390 original-instruction cases verify Init mutation, visible-list filtering,
guild0..3, all configured categories, HD behavior, threshold boundaries and
the original name truncation. The list-vector insertion and balance/count
getters are intercepted; the filter instructions execute unchanged.
`test_shop_offers.py` adds936 catalogue/Buy comparisons and encrypted exact-price,
replay, config-cache, restart and failed-Get tests. Native UI checks are deferred.
