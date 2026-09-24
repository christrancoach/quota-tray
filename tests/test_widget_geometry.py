from quota_widget.geometry import default_position, is_reachable, recover_position

PRIMARY = (0, 0, 2560, 1400)          # work area (taskbar excluded)
RIGHT = (2560, 0, 1920, 1040)          # second monitor to the right, different size / DPI
SIZE = (320, 180)


def test_saved_position_on_secondary_monitor_is_kept():
    assert recover_position((3000, 200), SIZE, [PRIMARY, RIGHT], PRIMARY) == (3000, 200)


def test_monitor_unplugged_snaps_back_to_primary():
    # Was on the right-hand monitor, which is gone now.
    pos = recover_position((3000, 200), SIZE, [PRIMARY], PRIMARY)
    assert pos == default_position(SIZE, PRIMARY)
    assert is_reachable((*pos, *SIZE), [PRIMARY])


def test_far_off_screen_and_negative_coordinates_snap_back():
    for saved in [(-5000, -5000), (99999, 10), (100, 5000)]:
        assert recover_position(saved, SIZE, [PRIMARY, RIGHT], PRIMARY) == default_position(SIZE, PRIMARY)


def test_mostly_off_screen_but_grabbable_is_kept():
    # 60 px still visible at the bottom-right edge: the user can drag it back themselves.
    saved = (2560 + 1920 - 60, 1040 - 60)
    assert recover_position(saved, SIZE, [PRIMARY, RIGHT], PRIMARY) == saved


def test_sliver_on_screen_is_not_enough():
    saved = (2560 + 1920 - 10, 100)  # only 10 px wide on screen
    assert recover_position(saved, SIZE, [PRIMARY, RIGHT], PRIMARY) == default_position(SIZE, PRIMARY)


def test_no_saved_position_uses_default():
    assert recover_position(None, SIZE, [PRIMARY], PRIMARY) == (2560 - 320 - 24, 96)   # clear of caption buttons
