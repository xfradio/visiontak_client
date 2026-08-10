from visiontak_client.branding import LOGO_BASENAME, logo_path


def test_snap_copy_wins_when_present(tmp_path):
    snap = tmp_path / "snap"
    (snap / "assets").mkdir(parents=True)
    logo = snap / "assets" / LOGO_BASENAME
    logo.write_bytes(b"\x89PNG\r\n\x1a\n")
    assert logo_path({"SNAP": str(snap)}) == logo


def test_missing_logo_is_not_an_error(tmp_path):
    """A display must still come up if the build shipped without artwork."""
    assert logo_path({"SNAP": str(tmp_path / "nowhere")}) in (None, logo_path({}))


def test_no_snap_env_falls_back_to_the_checkout():
    # Either the repo has assets/visiontak-logo.png or it does not; both are valid,
    # so assert the contract rather than the file's presence.
    result = logo_path({})
    assert result is None or result.name == LOGO_BASENAME
