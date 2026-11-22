from laptop_ai import nowplaying

XML = """<?xml version="1.0"?><root><state>playing</state><information><category name="meta">
<info name='filename'>Burna Boy - Last Last.mp3</info><info name='artist'>Burna Boy</info></category></information></root>"""


def test_parses_vlc_status():
    d = nowplaying.parse_status(XML)
    assert d == {"state": "playing", "title": "Burna Boy - Last Last", "artist": "Burna Boy"}
    assert nowplaying.parse_status("<root><state>stopped</state></root>")["state"] == "stopped"
    assert nowplaying.parse_status("<root><state>paused</state><information><category name='meta'><info name='title'>Song</info></category></information></root>")["title"] == "Song"


def test_current_is_none_when_vlc_is_not_there():
    def boom():
        raise OSError("refused")
    assert nowplaying.current(boom) is None
    assert nowplaying.current(lambda: XML)["state"] == "playing"
