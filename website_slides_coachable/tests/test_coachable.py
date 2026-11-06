import json

from odoo.exceptions import ValidationError
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged

GOOD_URL = "https://coachableplays.com/shared/bf1047e3925b3e5e3ed39409dd5b517c4e32ee0e5008b66f"


def create_quiz(env):
    channel = env["slide.channel"].create({"name": "Playbook", "enroll": "public", "visibility": "public", "is_published": True})
    slide = env["slide.slide"].create({
        "name": "Plays", "channel_id": channel.id, "slide_category": "quiz", "is_published": True,
    })
    question = env["slide.question"].create({
        "question": "What is this Play called?",
        "slide_id": slide.id,
        "coachable_url": GOOD_URL,
        "answer_ids": [
            (0, 0, {"text_value": "Disney BOW 31", "is_correct": True}),
            (0, 0, {"text_value": "Disney BOW 21", "is_correct": False}),
        ],
    })
    return slide, question


class TestCoachableLink(TransactionCase):
    def setUp(self):
        super().setUp()
        self.slide, self.question = create_quiz(self.env)

    def test_good_links(self):
        for url in (GOOD_URL, GOOD_URL + "/", GOOD_URL.replace("https://", "https://www.")):
            self.question.coachable_url = url
            self.assertTrue(self.question.coachable_preview)
        self.question.coachable_url = f"  {GOOD_URL}  "
        self.assertEqual(self.question.coachable_url, GOOD_URL)

    def test_bad_links_refused(self):
        for url in (
            "javascript:alert(1)",
            "http://coachableplays.com/shared/abc",
            "https://evil.example/shared/abc",
            "https://coachableplays.com.evil.example/shared/abc",
            "https://coachableplays.com/shared/abc?x=1",
            "https://coachableplays.com/shared/abc\"><script>alert(1)</script>",
            "https://coachableplays.com/account",
            "https://coachableplays.com/shared/../account",
        ):
            with self.assertRaises(ValidationError, msg=url):
                self.question.coachable_url = url

    def test_link_is_optional(self):
        self.question.coachable_url = False
        self.assertFalse(self.question.coachable_preview)

    def test_preview_only_for_valid_links_while_typing(self):
        draft = self.env["slide.question"].new({"coachable_url": "https://evil.example/x"})
        self.assertFalse(draft.coachable_preview)

    def test_link_copied_with_the_course_content(self):
        copy = self.question.copy({"slide_id": self.slide.id})
        self.assertEqual(copy.coachable_url, GOOD_URL)


@tagged("post_install", "-at_install")
class TestCoachableRoutes(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.slide, cls.question = create_quiz(cls.env)
        cls.plain = cls.env["slide.question"].create({
            "question": "No link here",
            "slide_id": cls.slide.id,
            "answer_ids": [
                (0, 0, {"text_value": "Yes", "is_correct": True}),
                (0, 0, {"text_value": "No", "is_correct": False}),
            ],
        })
        cls.officer = new_test_user(
            cls.env, "coach", password="coach_pass_1",
            groups="base.group_user,website_slides.group_website_slides_officer",
        )
        cls.slide.channel_id.user_id = cls.officer
        cls.student = new_test_user(cls.env, "player", password="player_pass_1", groups="base.group_portal")
        cls.slide.channel_id._action_add_members(cls.student.partner_id)

    def _rpc(self, route, **params):
        response = self.url_open(
            route,
            data=json.dumps({"jsonrpc": "2.0", "method": "call", "params": params}),
            headers={"Content-Type": "application/json"},
        )
        return response.json()

    def test_quiz_page_shows_the_frame_to_a_student(self):
        self.authenticate("player", "player_pass_1")
        response = self.url_open(f"/slides/slide/{self.slide.id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn(f'src="{GOOD_URL}"', response.text)
        self.assertIn('sandbox="allow-scripts allow-same-origin"', response.text)
        self.assertEqual(response.text.count("o_wslides_coachable_frame"), 1)

    def test_quiz_data_has_link_and_still_hides_answers(self):
        self.authenticate("player", "player_pass_1")
        result = self._rpc("/slides/slide/quiz/get", slide_id=self.slide.id)["result"]
        by_id = {question["id"]: question for question in result["slide_questions"]}
        self.assertEqual(by_id[self.question.id]["coachable_url"], GOOD_URL)
        self.assertFalse(by_id[self.plain.id]["coachable_url"])
        for answer in by_id[self.question.id]["answer_ids"]:
            self.assertIsNone(answer["is_correct"])

    def test_editing_on_the_website_keeps_the_link(self):
        self.authenticate("coach", "coach_pass_1")
        result = self._rpc(
            "/slides/slide/quiz/question_add_or_update",
            slide_id=self.slide.id,
            question="What is this Play called now?",
            sequence=1,
            answer_ids=[
                {"sequence": 1, "text_value": "A", "is_correct": True, "comment": ""},
                {"sequence": 2, "text_value": "B", "is_correct": False, "comment": ""},
            ],
            existing_question_id=self.question.id,
        )
        self.assertNotIn("error", result, result)
        self.assertIn(GOOD_URL, result["result"])
        new_question = self.slide.question_ids.filtered(
            lambda q: q.question == "What is this Play called now?"
        )
        self.assertEqual(new_question.coachable_url, GOOD_URL)
