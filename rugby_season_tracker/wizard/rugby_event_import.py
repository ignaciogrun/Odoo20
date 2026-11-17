import datetime
import io
import re

from markupsafe import Markup

from odoo import Command, fields, models
from odoo.exceptions import UserError

try:
    import openpyxl
except ImportError:  # pragma: no cover
    openpyxl = None

# Column titles on the coding sheet's "Event Log" tab -> what they hold.
COLUMNS = {
    "half": "half", "video time (m:ss)": "time", "video time": "time", "team": "side",
    "player #": "jersey", "event": "event", "category": "category", "quality": "quality",
    "offence detail": "offence", "try source": "try_source", "try origin": "try_origin",
    "try location": "try_location", "build-up rucks/mauls": "rucks", "build-up passes": "passes",
    "lineout numbers": "lineout", "carry meters": "carry", "kick meters": "kick", "notes": "notes",
}
OPTION_COLUMNS = {"offence": "offence", "try_source": "try_source", "try_origin": "try_origin",
                  "try_location": "try_location"}


def _text(value):
    return str(value).strip() if value is not None else ""


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


class RugbyEventImport(models.TransientModel):
    _name = "rugby.event.import"
    _description = "Import an Event Log from the Coding Sheet"

    match_id = fields.Many2one("rugby.match", required=True, ondelete="cascade")
    file = fields.Binary("Excel File", required=True)
    filename = fields.Char()
    replace = fields.Boolean(
        "Replace the Current Event Log", default=True,
        help="Removes the events already logged on this match first. Untick to add the file's rows after them.",
    )
    fill_lineup = fields.Boolean(
        "Add Players from the Player Stats Tab", default=True,
        help="Adds the jersey numbers and names typed on the Player Stats tab to the team sheet "
             "(numbers already on the team sheet are left alone).",
    )

    def _open(self):
        if not openpyxl:
            raise UserError(self.env._("The server is missing the openpyxl library needed to read Excel files."))
        try:
            return openpyxl.load_workbook(io.BytesIO(self.file.content), data_only=True, read_only=True)
        except Exception as error:
            raise UserError(self.env._("This file can't be read. Save it as an Excel workbook (.xlsx) and try again.")) from error

    def _find_header(self, sheet, needed):
        """The first of the top rows holding every needed title: (row number, {title: column index})."""
        for number, row in enumerate(sheet.iter_rows(min_row=1, max_row=15, values_only=True), start=1):
            titles = {_text(cell).lower(): index for index, cell in enumerate(row) if _text(cell)}
            if needed <= set(titles):
                return number, titles
        return None, {}

    def _video_time(self, value):
        if isinstance(value, datetime.time):
            # Excel read "3:04" as a clock time (hours:minutes).
            return f"{value.hour}:{value.minute:02d}"
        if isinstance(value, datetime.timedelta):
            seconds = int(value.total_seconds())
            return f"{seconds // 60}:{seconds % 60:02d}"
        return _text(value)

    def action_import(self):
        self.ensure_one()
        match = self.match_id
        workbook = self._open()
        names = {name.lower(): name for name in workbook.sheetnames}
        if "event log" not in names:
            raise UserError(self.env._("This workbook has no \"Event Log\" tab. Use the film-review coding sheet."))
        sheet = workbook[names["event log"]]
        header, titles = self._find_header(sheet, {"half", "event"})
        if not header:
            raise UserError(self.env._("The Event Log tab has no row with the column titles (Half, Event, ...)."))
        columns = {COLUMNS[title]: index for title, index in titles.items() if title in COLUMNS}

        Type, Category, Option = self.env["rugby.event.type"], self.env["rugby.event.category"], self.env["rugby.event.option"]
        types = {event_type.name.lower(): event_type for event_type in Type.with_context(active_test=False).search([])}
        options = {(option.kind, option.name.lower()): option for option in Option.with_context(active_test=False).search([])}
        created_types = []
        vals_list, problems = [], []
        sequence = 0 if self.replace else max(match.event_ids.mapped("sequence"), default=0)
        for number, row in enumerate(sheet.iter_rows(min_row=header + 1, values_only=True), start=header + 1):
            def cell(name, row=row):
                index = columns.get(name)
                return row[index] if index is not None and index < len(row) else None

            event_name = _text(cell("event"))
            if not event_name:
                continue
            event_type = types.get(event_name.lower())
            if not event_type:
                # An event renamed or added on the sheet's Lists tab: add it here too.
                category_name = _text(cell("category"))
                category = category_name and (
                    Category.search([("name", "=ilike", category_name)], limit=1) or Category.create({"name": category_name}))
                event_type = Type.create({
                    "name": event_name, "sequence": 1000,
                    "category_ids": [Command.set(category.ids)] if category else False,
                })
                types[event_name.lower()] = event_type
                created_types.append(event_name)
            half = _text(cell("half")).upper()[:1]
            if half not in ("1", "2"):
                problems.append(self.env._("Row %(row)s: Half is \"%(value)s\" (use 1H or 2H).", row=number, value=_text(cell("half"))))
                continue
            time = self._video_time(cell("time"))
            if time and not re.match(r"^\d{1,3}:[0-5]\d$", time):
                problems.append(self.env._("Row %(row)s: Video Time is \"%(value)s\" (use minutes:seconds, like 3:04).", row=number, value=time))
                continue
            sequence += 1
            vals = {
                "match_id": match.id, "sequence": sequence, "half": half, "video_time": time or False,
                "side": "opp" if _text(cell("side")).lower().startswith("opp") else "own",
                "jersey_number": int(_number(cell("jersey"))), "event_type_id": event_type.id,
                "quality": _text(cell("quality")).lower() if _text(cell("quality")).lower() in ("positive", "neutral", "negative") else False,
                "buildup_rucks": int(_number(cell("rucks"))), "buildup_passes": int(_number(cell("passes"))),
                "lineout_numbers": int(_number(cell("lineout"))),
                "carry_meters": _number(cell("carry")), "kick_meters": _number(cell("kick")),
                "notes": _text(cell("notes")) or False,
            }
            for column, kind in OPTION_COLUMNS.items():
                label = _text(cell(column))
                if label:
                    option = options.get((kind, label.lower()))
                    if not option:
                        option = options[kind, label.lower()] = Option.create({"name": label, "kind": kind, "sequence": 1000})
                    vals[f"{column}_id"] = option.id
            vals_list.append(vals)
        if problems:
            raise UserError(self.env._("Nothing was imported. Fix these rows on the sheet and try again:") + "\n" + "\n".join(problems[:20]))
        if not vals_list:
            raise UserError(self.env._("The Event Log tab has no events."))

        added_players = self._fill_lineup(workbook, names) if self.fill_lineup else 0
        self._set_recording(workbook, names)
        if self.replace:
            match.event_ids.unlink()
        self.env["rugby.match.event"].create(vals_list)

        message = self.env._("%s events imported.", len(vals_list))
        if added_players:
            message += " " + self.env._("%s players added to the team sheet.", added_players)
        if created_types:
            message += " " + self.env._("New events added to the list: %s.", ", ".join(created_types))
        match.message_post(body=message)
        return {
            "type": "ir.actions.client", "tag": "display_notification",
            "params": {"type": "success", "message": message, "next": {"type": "ir.actions.act_window_close"}},
        }

    def _fill_lineup(self, workbook, names):
        """Team sheet lines from the Player Stats tab (jersey number + typed name)."""
        if "player stats" not in names:
            return 0
        sheet = workbook[names["player stats"]]
        header, titles = self._find_header(sheet, {"#", "player name"})
        if not header:
            return 0
        match, Partner = self.match_id, self.env["res.partner"]
        taken = set(match.lineup_ids.mapped("jersey_number"))
        listed = match.lineup_ids.player_id
        lines = []
        for row in sheet.iter_rows(min_row=header + 1, values_only=True):
            jersey, name = int(_number(row[titles["#"]])), _text(row[titles["player name"]])
            if not jersey or not name or jersey in taken:
                continue
            player = Partner.search([("name", "=ilike", name), ("is_company", "=", False)], limit=1)
            if not player:
                player = Partner.create({"name": name, "is_company": False})
            if player in listed:
                continue
            taken.add(jersey)
            listed |= player
            lines.append({"match_id": match.id, "jersey_number": jersey, "player_id": player.id})
        self.env["rugby.match.player"].create(lines)
        return len(lines)

    def _set_recording(self, workbook, names):
        """The Guide tab's video address becomes the match recording, if none is set yet."""
        match = self.match_id
        if match.recording_url or "guide" not in names:
            return
        for row in workbook[names["guide"]].iter_rows(min_row=1, max_row=20, max_col=2, values_only=True):
            label, value = _text(row[0]).lower(), _text(row[1] if len(row) > 1 else None)
            if label.startswith("video url") and value.startswith("http"):
                match.recording_link = Markup('<p><a href="%s">%s</a></p>') % (value, self.env._("Match recording"))
                return
