"""Widget-level tests, run against Qt's offscreen platform.

These are not pixel tests. They check that the window wires up, that the
tabs show what the calculation layer says they should, and that the
interactions the acceptance checklist describes actually do something.
"""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal

import pytest

from app.core.models import EntryKind, TravelDetail
from tests.conftest import sast

pytestmark = pytest.mark.usefixtures("qapp")


def today_for(repo) -> _dt.date:
    return _dt.datetime.now(tz=repo.timezone()).date()


def at(repo, hour: int, minute: int = 0) -> _dt.datetime:
    """A time on today's date, in the display timezone."""
    day = today_for(repo)
    return _dt.datetime.combine(
        day, _dt.time(hour, minute), tzinfo=repo.timezone()
    )


class TestWindowConstruction:
    def test_the_window_has_the_five_tabs(self, window):
        labels = [window.tabs.tabText(i) for i in range(window.tabs.count())]
        assert labels == ["Today", "Projects", "Log", "Review && Submit", "Settings"]

    def test_the_status_bar_says_where_the_database_is(self, window):
        assert "Database:" in window.status_left.text()
        assert "timetrack.db" in window.status_left.text()

    def test_both_channels_start_idle(self, window):
        assert window.now_strip.work_panel.elapsed.text() == "0:00:00"
        assert window.now_strip.software_panel.elapsed.text() == "0:00:00"
        assert not window.now_strip.work_panel.stop_button.isEnabled()


class TestNowStrip:
    def test_starting_work_updates_the_readout(self, window, repo, project):
        window.refresh_all()
        window._start_work(project, None)  # noqa: SLF001
        window.now_strip.update_display(window.timers)
        assert window.now_strip.work_panel.stop_button.isEnabled()
        assert "Kloof" in window.now_strip.work_panel.what.text()

    def test_both_channels_can_run_at_once(self, window, repo, project):
        window._start_work(project, None)  # noqa: SLF001
        window._start_software(project, "RS2")  # noqa: SLF001
        window.now_strip.update_display(window.timers)
        assert window.timers.is_running(EntryKind.WORK)
        assert window.timers.is_running(EntryKind.SOFTWARE)
        assert "RS2" in window.now_strip.software_panel.what.text()

    def test_pausing_is_shown_on_the_panel(self, window, project):
        window._start_work(project, None)  # noqa: SLF001
        window._toggle_pause(EntryKind.WORK)  # noqa: SLF001
        window.now_strip.update_display(window.timers)
        assert "paused" in window.now_strip.work_panel.what.text()
        assert window.now_strip.work_panel.pause_button.text() == "Resume"

    def test_starting_software_without_a_package_is_refused(self, window, project, monkeypatch):
        warned: list[str] = []
        monkeypatch.setattr(
            "app.ui.main_window.warn", lambda parent, message: warned.append(message)
        )
        window._start_software(project, "")  # noqa: SLF001
        assert warned and "package" in warned[0].lower()
        assert not window.timers.is_running(EntryKind.SOFTWARE)

    def test_archived_projects_leave_the_picker(self, window, repo, project):
        window.refresh_all()
        assert window.now_strip.project.count() == 1
        repo.archive_project(project)
        window.refresh_all()
        assert window.now_strip.project.count() == 0


class TestTodayTab:
    def test_todays_entries_are_listed_with_totals(self, window, repo, project):
        repo.add_manual_entry(
            project,
            started_at=at(repo, 9),
            ended_at=at(repo, 10, 40),
            description="Slope stability analysis",
        )
        window.tabs.setCurrentWidget(window.today_tab)
        window.today_tab.refresh()

        assert window.today_tab.table.rowCount() == 1
        # 1h40m raw, billing 1.75 once rounded up to the next quarter.
        assert "1:40" in window.today_tab.totals.text()
        assert "1.75" in window.today_tab.totals.text()

    def test_editing_a_description_in_place_is_saved_and_audited(
        self, window, repo, project
    ):
        entry_id = repo.add_manual_entry(
            project, started_at=at(repo, 9), ended_at=at(repo, 10), description="Rough"
        )
        window.today_tab.refresh()

        from app.ui.tab_today import COL_DESC

        item = window.today_tab.table.item(0, COL_DESC)
        item.setText("Slope stability review")

        assert repo.get_entry(entry_id).description == "Slope stability review"
        reasons = [row["reason"] for row in repo.audit_trail(entry_id)]
        assert "edited" in reasons

    def test_editing_the_end_time_in_place_recomputes_the_duration(
        self, window, repo, project
    ):
        entry_id = repo.add_manual_entry(
            project, started_at=at(repo, 9), ended_at=at(repo, 10)
        )
        window.today_tab.refresh()

        from app.ui.tab_today import COL_END

        window.today_tab.table.item(0, COL_END).setText("11:30")
        assert repo.get_entry(entry_id).duration_seconds == int(2.5 * 3600)

    def test_a_bad_time_is_rejected_with_a_readable_message(
        self, window, repo, project, monkeypatch
    ):
        entry_id = repo.add_manual_entry(
            project, started_at=at(repo, 9), ended_at=at(repo, 10)
        )
        window.today_tab.refresh()

        warned: list[str] = []
        monkeypatch.setattr(
            "app.ui.tab_today.warn", lambda parent, message: warned.append(message)
        )
        from app.ui.tab_today import COL_END

        window.today_tab.table.item(0, COL_END).setText("half past ten")
        assert warned and "24-hour" in warned[0]
        assert repo.get_entry(entry_id).duration_seconds == 3600  # unchanged

    def test_a_running_timer_is_shown_but_not_editable(self, window, project):
        window._start_work(project, None)  # noqa: SLF001
        window.today_tab.refresh()

        from PySide6.QtCore import Qt

        from app.ui.tab_today import COL_DESC

        item = window.today_tab.table.item(0, COL_DESC)
        assert not item.flags() & Qt.ItemFlag.ItemIsEditable


class TestReviewTab:
    def _seed_three_days(self, repo, project):
        base = today_for(repo) - _dt.timedelta(days=3)
        for offset in range(3):
            day = base + _dt.timedelta(days=offset)
            start = _dt.datetime.combine(
                day, _dt.time(9, 0), tzinfo=repo.timezone()
            )
            repo.add_manual_entry(
                project,
                started_at=start,
                ended_at=start + _dt.timedelta(hours=2, minutes=10),
                description=f"Day {offset}",
            )

    def test_one_row_per_date_with_the_billed_figure(self, window, repo, project):
        self._seed_three_days(repo, project)
        window.tabs.setCurrentWidget(window.review_tab)
        window.review_tab.refresh()

        from app.ui.tab_review import COL_HOURS

        assert window.review_tab.table.rowCount() == 3
        # 2h10m bills 2.25.
        assert window.review_tab.table.item(0, COL_HOURS).text() == "2.25"

    def test_ticking_a_date_persists(self, window, repo, project):
        """Acceptance checklist 18."""
        self._seed_three_days(repo, project)
        window.review_tab.refresh()

        from PySide6.QtCore import Qt

        from app.ui.tab_review import COL_DATE, COL_TICK
        from app.ui.widgets import ID_ROLE

        ordinal = window.review_tab.table.item(0, COL_DATE).data(ID_ROLE)
        window.review_tab.table.item(0, COL_TICK).setCheckState(Qt.CheckState.Checked)

        day = _dt.date.fromordinal(int(ordinal))
        assert repo.get_daily_note(project, day)["ticked_at"] is not None
        # ... and survives a rebuild of the table
        window.review_tab.refresh()
        assert (
            window.review_tab.table.item(0, COL_TICK).checkState()
            == Qt.CheckState.Checked
        )
        assert "1 of 3" in window.review_tab.progress.text()

    def test_the_next_unticked_row_is_highlighted(self, window, repo, project):
        self._seed_three_days(repo, project)
        window.review_tab.refresh()

        from PySide6.QtCore import Qt
        from PySide6.QtGui import QColor

        from app.ui.tab_review import COL_DATE, COL_TICK, next_row_background

        window.review_tab.table.item(0, COL_TICK).setCheckState(Qt.CheckState.Checked)
        window.review_tab.refresh()

        highlighted = window.review_tab.table.item(1, COL_DATE).background().color()
        assert highlighted == QColor(next_row_background())

    def test_a_written_description_replaces_the_generated_one(
        self, window, repo, project
    ):
        self._seed_three_days(repo, project)
        window.review_tab.refresh()

        from app.ui.tab_review import COL_DATE, COL_DESC
        from app.ui.widgets import ID_ROLE

        ordinal = window.review_tab.table.item(0, COL_DATE).data(ID_ROLE)
        window.review_tab.table.item(0, COL_DESC).setText("My own narrative")

        day = _dt.date.fromordinal(int(ordinal))
        assert (
            repo.get_daily_note(project, day)["description_override"]
            == "My own narrative"
        )
        window.review_tab.refresh()
        assert window.review_tab.table.item(0, COL_DESC).text() == "My own narrative"

    def test_marking_the_period_submitted(self, window, repo, project):
        self._seed_three_days(repo, project)
        window.review_tab.refresh()
        start, end = window.review_tab._period_start, window.review_tab._period_end  # noqa: SLF001

        repo.mark_submitted(project, start, end)
        window.review_tab.refresh()
        assert "submitted" in window.review_tab.progress.text()
        assert window.review_tab.submit_button.text() == "Undo 'submitted'"


class TestBannersAndGaps:
    def test_the_catch_up_banner_appears_when_weekdays_are_empty(
        self, window, repo, project
    ):
        # A database with one entry a long time ago leaves plenty of gaps.
        repo.add_manual_entry(
            project, started_at=sast(2026, 1, 5, 9), ended_at=sast(2026, 1, 5, 17)
        )
        window._show_startup_banners()  # noqa: SLF001
        texts = [
            window.banner_area.itemAt(i).widget().label.text()
            for i in range(window.banner_area.count())
        ]
        assert any("no time logged" in text for text in texts)

    def test_no_banner_when_every_weekday_is_covered(self, window, repo, project):
        today = today_for(repo)
        for offset in range(0, 40):
            day = today - _dt.timedelta(days=offset)
            start = _dt.datetime.combine(day, _dt.time(9), tzinfo=repo.timezone())
            repo.add_manual_entry(
                project, started_at=start, ended_at=start + _dt.timedelta(hours=8)
            )
        window._show_startup_banners()  # noqa: SLF001
        texts = [
            window.banner_area.itemAt(i).widget().label.text()
            for i in range(window.banner_area.count())
        ]
        assert not any("no time logged" in text for text in texts)


class TestCloseBehaviour:
    def test_the_running_summary_names_the_timer_and_its_length(
        self, window, project, repo
    ):
        """Acceptance checklist 10: the close dialog must name what is running."""
        window._start_work(project, None)  # noqa: SLF001
        summary = window._running_summary()  # noqa: SLF001
        assert len(summary) == 1
        assert "Kloof" in summary[0]
        assert "running for" in summary[0]

    def test_nothing_is_running_means_an_empty_summary(self, window):
        assert window._running_summary() == []  # noqa: SLF001

    def test_shutdown_stops_running_timers_rather_than_abandoning_them(
        self, window, repo, project
    ):
        entry_id = window.timers.start_work(project)
        window._shutdown()  # noqa: SLF001
        entry = repo.get_entry(entry_id)
        assert not entry.is_running
        assert entry.ended_at is not None


class TestTray:
    def test_the_tray_tooltip_names_what_is_running(self, window, project):
        window._start_work(project, None)  # noqa: SLF001
        window._update_tray()  # noqa: SLF001
        tooltip = window.tray.icon.toolTip()
        assert "Work:" in tooltip and "Kloof" in tooltip

    def test_the_tray_says_so_when_nothing_is_running(self, window):
        window._update_tray()  # noqa: SLF001
        assert "No timer running" in window.tray.icon.toolTip()

    def test_stop_all_is_only_offered_when_something_runs(self, window, project):
        window._update_tray()  # noqa: SLF001
        assert not window.tray.stop_all_action.isEnabled()
        window._start_work(project, None)  # noqa: SLF001
        window._update_tray()  # noqa: SLF001
        assert window.tray.stop_all_action.isEnabled()


class TestTravelThroughTheGui:
    def test_a_trip_logged_with_odometer_readings_reaches_the_database(
        self, window, repo, project
    ):
        entry_id = repo.log_travel(
            project,
            on_date=today_for(repo),
            travel=TravelDetail(
                odo_start=Decimal("104200"),
                odo_end=Decimal("104320"),
                trip_to="Kloof TSF",
                trip_purpose="Site inspection",
            ),
            description="Site trip",
        )
        window.today_tab.refresh()

        from app.ui.tab_today import COL_KM

        assert repo.get_entry(entry_id).travel.km_travelled == Decimal("120.0")
        assert window.today_tab.table.item(0, COL_KM).text() == "120.0"
        assert "120.0 km" in window.today_tab.totals.text()


class TestTimezoneSetting:
    """The display timezone had no control, so the setting was unreachable.

    It matters on Windows in particular: without a named zone the
    application uses the operating system's current offset, which is right
    all year in South Africa but would drift in a country that changes its
    clocks.
    """

    def test_the_picker_offers_the_system_default_first(self, window):
        picker = window.settings_tab.timezone
        assert picker.itemData(0) == "system"
        assert "computer" in picker.itemText(0).lower()

    def test_real_timezones_are_listed(self, window):
        picker = window.settings_tab.timezone
        assert picker.findData("Africa/Johannesburg") > 0
        assert picker.count() > 100

    def test_choosing_a_zone_saves_it_and_the_app_uses_it(self, window, repo):
        from zoneinfo import ZoneInfo

        picker = window.settings_tab.timezone
        picker.setCurrentIndex(picker.findData("Europe/London"))

        assert repo.get_setting("display.timezone") == "Europe/London"
        assert repo.timezone() == ZoneInfo("Europe/London")

    def test_it_reports_what_the_choice_resolves_to(self, window, repo):
        picker = window.settings_tab.timezone
        picker.setCurrentIndex(picker.findData("Africa/Johannesburg"))
        assert "UTC+02:00" in window.settings_tab.timezone_status.text()

    def test_the_setting_survives_a_reload(self, window, repo):
        picker = window.settings_tab.timezone
        picker.setCurrentIndex(picker.findData("Africa/Johannesburg"))
        window.settings_tab.reload()
        assert picker.currentData() == "Africa/Johannesburg"


class TestRunningTimerDisplay:
    def test_a_running_entry_shows_live_elapsed_not_zero(self, window, repo, project):
        """The stored duration only updates on the heartbeat, so a timer
        started moments ago would otherwise read 0:00 in the table while the
        big readout above it counted up."""
        import datetime as _dt

        from app.ui.tab_today import COL_DURATION

        started = _dt.datetime.now(tz=repo.timezone()) - _dt.timedelta(minutes=95)
        entry_id = repo.start_timer(project, now=started)
        assert repo.get_entry(entry_id).duration_seconds == 0  # no heartbeat yet

        window.today_tab.refresh()
        assert window.today_tab.table.item(0, COL_DURATION).text() == "1:35"

    def test_a_stopped_entry_shows_its_stored_duration(self, window, repo, project):
        from app.ui.tab_today import COL_DURATION

        repo.add_manual_entry(
            project, started_at=at(repo, 9), ended_at=at(repo, 11, 30)
        )
        window.today_tab.refresh()
        assert window.today_tab.table.item(0, COL_DURATION).text() == "2:30"


class TestRemovingProjectsFromTheList:
    def _select_project(self, window, project_id):
        from app.ui.tab_projects import COL_NAME
        from app.ui.widgets import ID_ROLE

        window.projects_tab.refresh()
        table = window.projects_tab.table
        for row in range(table.rowCount()):
            if table.item(row, COL_NAME).data(ID_ROLE) == project_id:
                table.selectRow(row)
                return
        raise AssertionError("project not in the table")

    def test_the_remove_button_is_offered_for_an_unused_project(self, window, repo):
        spare = repo.add_project("Pasted by mistake")
        self._select_project(window, spare)
        assert window.projects_tab.remove_button.isEnabled()

    def test_it_is_disabled_for_a_project_with_time_on_it(self, window, repo, project):
        repo.add_manual_entry(
            project, started_at=at(repo, 9), ended_at=at(repo, 11)
        )
        self._select_project(window, project)
        assert not window.projects_tab.remove_button.isEnabled()
        assert "Mark done" in window.projects_tab.remove_button.toolTip()

    def test_removing_takes_it_off_the_list(self, window, repo, monkeypatch):
        from PySide6.QtWidgets import QMessageBox

        spare = repo.add_project("Pasted by mistake")
        self._select_project(window, spare)
        monkeypatch.setattr(
            QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes
        )
        window.projects_tab.remove_project()

        assert repo.get_project(spare) is None
        assert window.projects_tab.table.rowCount() == 0

    def test_saying_no_keeps_it(self, window, repo, monkeypatch):
        from PySide6.QtWidgets import QMessageBox

        spare = repo.add_project("Pasted by mistake")
        self._select_project(window, spare)
        monkeypatch.setattr(
            QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No
        )
        window.projects_tab.remove_project()
        assert repo.get_project(spare) is not None


class TestTheme:
    def test_the_picker_offers_system_light_and_dark(self, window):
        picker = window.settings_tab.theme
        assert [picker.itemData(i) for i in range(picker.count())] == [
            "system",
            "light",
            "dark",
        ]

    def test_choosing_dark_applies_it(self, window, repo, qapp):
        from app.ui import theme

        picker = window.settings_tab.theme
        picker.setCurrentIndex(picker.findData(theme.DARK))

        assert repo.get_setting("display.theme") == theme.DARK
        assert theme.current_theme() == theme.DARK
        assert theme.PALETTES[theme.DARK]["panel"] in qapp.styleSheet()

    def test_switching_back_to_light_repaints(self, window, repo, qapp):
        from app.ui import theme

        picker = window.settings_tab.theme
        picker.setCurrentIndex(picker.findData(theme.DARK))
        picker.setCurrentIndex(picker.findData(theme.LIGHT))

        assert theme.current_theme() == theme.LIGHT
        assert theme.PALETTES[theme.LIGHT]["panel"] in qapp.styleSheet()

    def test_notes_are_styled_by_role_so_they_follow_the_theme(self, window):
        """An inline colour would survive the switch and leave grey on grey."""
        hint = window.today_tab.findChild(type(window.status_left))
        roles = [
            child.property("role")
            for child in window.settings_tab.findChildren(type(window.status_left))
        ]
        assert "muted" in roles


class TestBannerAppearance:
    def test_the_banner_paints_its_own_background(self, qapp):
        """A plain QWidget ignores a stylesheet background unless asked to,
        which silently made the catch-up banner colourless."""
        from PySide6.QtCore import Qt

        from app.ui.widgets import Banner

        banner = Banner("6 weekdays have no time logged", "Show me")
        assert banner.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        assert banner.objectName() == "TimeTrackBanner"

    def test_the_tone_is_a_property_the_stylesheet_can_select(self, qapp):
        from app.ui.widgets import Banner

        assert Banner("x", tone="danger").property("tone") == "danger"
        assert Banner("x").property("tone") == "warning"

    def test_both_tones_appear_in_the_stylesheet(self, qapp):
        from app.ui import theme

        sheet = theme.stylesheet()
        assert "QWidget#TimeTrackBanner" in sheet
        assert 'QWidget#TimeTrackBanner[tone="danger"]' in sheet


def test_windows_do_not_accumulate_between_tests(qapp, repo, tmp_path, monkeypatch):
    """Guards the fixture teardown.

    Qt holds DeferredDelete events until the event loop that posted them
    returns, so a queued deleteLater() never fires in a test. Every window
    then stays alive and every stylesheet change has to re-polish all of
    them, which turned a 0.2 second theme switch into nineteen seconds.
    """
    from PySide6.QtCore import QCoreApplication, QEvent, QSettings
    from PySide6.QtWidgets import QApplication

    from app.ui.main_window import MainWindow

    monkeypatch.setattr(QSettings, "value", lambda self, *a, **k: None, raising=False)
    monkeypatch.setattr(QSettings, "setValue", lambda self, *a, **k: None, raising=False)
    repo.set_setting("workbook.path", str(tmp_path / "workbook"))

    def build_and_drop():
        window = MainWindow(repo)
        window.autosave.synchronous = True
        window.timers.shutdown()
        window.autosave.shutdown()
        window.tray.hide()
        window.hide()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qapp.processEvents()

    build_and_drop()
    after_first = len(QApplication.allWidgets())
    for _ in range(3):
        build_and_drop()
    after_more = len(QApplication.allWidgets())

    # A few stragglers are tolerable; three more whole windows are not.
    assert after_more - after_first < 100, (
        f"widgets grew from {after_first} to {after_more} over three windows"
    )
