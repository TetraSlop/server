# Event files and control builders

event_service.py creates the three exact requested file extents: EventList2C08,
EventDataList1308 and EventMemberList2808. Server.profile_file exposes empty
compatible directory/member lists by default, so browsing does not claim a
hosted tournament. Active event admission, matches and mission awards are not
enabled by these file builders.

Explicit configured builders preserve opaque template bytes and validate the
original fixed arrays, key/name/text terminators, class/board selector domains,
nine prize-display rows and first-three mission presentation. Prize display is
not an authoritative grant. Time replies contain all three fields; announcements
use complete ascending page groups within ten199-byte text buffers and the
447-byte named-body limit.

Seven tests exercise bounds/template preservation and original x86 lookup,
class/schedule/prize/mission projection, time subtraction and long announcement
assembly. Sources: EVENT_CONTRACT.md and FILE_COVERAGE.md in protocol_map.

Later implementation adds configured E/F advertisement/admission, borrowed-card
matches and observers, durable results/progress and F-card collection. See
EVENT_CONFIGURATION.md and INTEGRATION_STATUS.md for current coverage.
Historical scheduling, automatic mission/award policy and opaque metadata remain
unproved; local progression/grants are explicit host policies.
