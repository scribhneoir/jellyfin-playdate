-- Edit these values before building for hardware. Simulator key=value arguments
-- override them. A nil host plays the bundled, generated diagnostic file.
PDS_PROBE_CONFIG = {
    seconds = 20,
    host = nil, -- e.g. '192.168.1.10' (the Docker machine's LAN address)
    port = 8000,
    path = '/stream.pds',
    file = 'fixture.pds',
}
