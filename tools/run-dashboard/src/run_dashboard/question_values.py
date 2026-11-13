"""requests.jsonl の問いと答えから、画面に出す値 (問いの値、adopted、操作に採った数) を求める純粋な関数。

操作の決め方は第4章の switch2_agent.action_decision の関数 (check_answers・adopt_buttons・
remove_opposite_dpad・choose_direction) を呼んで求め、同じ計算を書き直さない。
問いの target は header に無いため、switch2_agent.questions.build_questions() の同じ name の問いから取る。

古い形の実行 (is_old_format が真) は、問いの形が今の第4章と違うため、問いの値と adopted を求めない。古い形は次の 2 つ:
    - header の問いに answer_type が無い形 (ボタンの押下と短押しが、別々の確率の問い press_<ボタン>・tap_<ボタン>)
    - header の問いに advisor の役割ごとの 4 問 (advisor_controls・advisor_strategy・advisor_advice・
      advisor_tips) がそろっていない形 (advisor の問いが name `advisor` の 1 問)
I/O を持たない。
"""

from collections.abc import Mapping

from switch2_agent.action_decision import (
    adopt_buttons,
    check_answers,
    choose_direction,
    remove_opposite_dpad,
)
from switch2_agent.advisor_roles import ADVISOR_ROLES
from switch2_agent.questions import NONE_CHOICE, Choice, Question, build_questions

from run_dashboard.jsonl_lines import is_json_number

# adopted の値のうち、操作に採らなかったことを表す値 (ボタンが十字キーの相反する方向に負けた)
REJECTED = "rejected"
# adopted の値のうち、advisor に求めたことを表す値 (advisor の問いの確率がしきい値を超えた)
REQUESTED = "requested"


def is_old_format(header: dict | None) -> bool:
    """requests.jsonl の header が古い形の実行のものなら True を返す。

    header が None なら False (requests.jsonl の無い実行は、今の問いの一覧で扱う)。
    header の questions がリストでなければ True。questions の要素のどれかが、answer_type のキーを持つ object (辞書)
    でなければ True。questions の name に、advisor_roles.ADVISOR_ROLES のすべての question_name
    (advisor_controls・advisor_strategy・advisor_advice・advisor_tips) がそろっていなければ True。
    それ以外は False。
    """
    if header is None:
        return False
    questions = header.get("questions")
    if not isinstance(questions, list):
        return True
    if any(
        not isinstance(question, dict) or "answer_type" not in question
        for question in questions
    ):
        return True
    names = {question.get("name") for question in questions}
    return not all(role.question_name in names for role in ADVISOR_ROLES)


def header_question_list(header: dict | None) -> list[dict]:
    """summary の questions の一覧 (問いの値 p の並びの順でもある) を返す。

    各要素は {"name", "text", "kind", "answer_type", "choices": [{"name", "description"}, ...]} (header の問いの順。
    確率の問いの choices は空のリスト)。
    header が None なら、switch2_agent.questions.build_questions() の問いから同じ形で作る
    (choices は Question.choices の Choice の name と description)。
    is_old_format(header) が真なら空のリストを返す。
    """
    if header is None:
        return [_question_item(question) for question in build_questions()]
    if is_old_format(header):
        return []
    return [_header_question_item(question) for question in header["questions"]]


def _question_item(question: Question) -> dict:
    """第4章の Question を、header_question_list の要素の形にする。"""
    return {
        "name": question.name,
        "text": question.text,
        "kind": question.kind,
        "answer_type": question.answer_type,
        "choices": [
            {"name": choice.name, "description": choice.description}
            for choice in question.choices
        ],
    }


def _header_question_item(question: dict) -> dict:
    """header の問い 1 つを、header_question_list の要素の形 (5 つのキーだけ) にする。

    choices がリストでなければ空のリスト、その中の object でない要素は入れない。
    """
    choices = question.get("choices")
    return {
        "name": question.get("name"),
        "text": question.get("text"),
        "kind": question.get("kind"),
        "answer_type": question.get("answer_type"),
        "choices": [
            {"name": choice.get("name"), "description": choice.get("description")}
            for choice in (choices if isinstance(choices, list) else [])
            if isinstance(choice, dict)
        ],
    }


def questions_for_decision(question_list: list[dict]) -> list[Question]:
    """header_question_list の一覧を、第4章の判定の関数に渡す Question の一覧にする (question_list の順)。

    switch2_agent.questions.build_questions() の問いと name で突き合わせ、target はその問いの値、
    text・kind・answer_type は question_list の値、choices は question_list の choices の
    switch2_agent.questions.Choice(name, description) のタプルで作る。
    build_questions() に無い name の問いは入れない (その問いの adopted は求めない)。
    """
    built = {question.name: question for question in build_questions()}
    questions = []
    for item in question_list:
        base = built.get(item["name"])
        if base is None:
            continue
        questions.append(
            Question(
                name=base.name,
                text=item["text"],
                kind=item["kind"],
                target=base.target,
                answer_type=item["answer_type"],
                choices=tuple(
                    Choice(choice["name"], choice["description"])
                    for choice in item["choices"]
                ),
            )
        )
    return questions


def question_value(
    question: dict,
    probabilities: Mapping | None,
    choices: Mapping | None,
) -> float | None:
    """問い 1 つの値を返す。question は header_question_list の要素。

    - 選択肢の問い (answer_type が "choice"): choices[name] の、none (switch2_agent.questions.NONE_CHOICE) 以外の
      選択肢 (question の choices の name のうち none 以外) の確率の合計。ボタンなら hold + tap、
      スティックの方向なら 2 方向の合計
    - 確率の問い (answer_type が "probability"): probabilities[name]
    答えが無い (probabilities か choices が None、name が無い、足す選択肢が 1 つでも無い) か、値が数
    (bool を除く int と float) でなければ None を返す。answer_type がそのどちらでもない問いも None を返す。
    """
    name = question["name"]
    if question["answer_type"] == "choice":
        values = choices.get(name) if choices is not None else None
        if not isinstance(values, Mapping):
            return None
        total = 0.0
        for choice in question["choices"]:
            if choice["name"] == NONE_CHOICE:
                continue
            value = values.get(choice["name"])
            if not is_json_number(value):
                return None
            total += value
        return total
    if question["answer_type"] == "probability":
        value = probabilities.get(name) if probabilities is not None else None
        return float(value) if is_json_number(value) else None
    return None


def request_question_values(
    question_list: list[dict], request: dict
) -> list[float | None] | None:
    """要求の行 request の、question_list の順の問いの値 (question_value) のリストを返す。

    request の probabilities と choices がどちらも null (応答が無い要求) なら None を返す。
    object でない方は None として question_value に渡す。
    """
    probabilities = request.get("probabilities")
    choices = request.get("choices")
    if probabilities is None and choices is None:
        return None
    return [
        question_value(
            question,
            probabilities if isinstance(probabilities, Mapping) else None,
            choices if isinstance(choices, Mapping) else None,
        )
        for question in question_list
    ]


def adopted_answers(
    questions: list[Question],
    probabilities: Mapping,
    choices: Mapping,
    threshold: float,
) -> dict[str, str | None] | None:
    """答えから、問いの name → adopted の値の辞書を返す (questions の順)。threshold は advisor の問いのしきい値。

    先に switch2_agent.action_decision.check_answers(probabilities, choices, questions) を呼び、ValueError なら
    (答えの欠けた応答) None を返す。adopted の値は、第4章の decide_action と同じ決め方で、問いの kind ごとに決める:
        - button: initial = adopt_buttons(choices, questions)、kept = dict(initial) に remove_opposite_dpad(kept)
          をした結果とする。問いの target が kept にあればその種類 ("hold" か "tap")、initial にあって kept に無ければ
          "rejected" (押す側の合計が none より大きかったが、十字キーの相反する方向に負けて採らなかった)、
          それ以外は None (押さない)
        - stick_direction: choose_direction(問い, choices[name]) が none なら None、そうでなければその選択肢の name
          (none は、none の確率が最大のときか、最大の確率が並んだとき)
        - stick_strength: None
        - advisor: probabilities[name] > threshold なら "requested"、それ以外 (しきい値ちょうどを含む) は None
    """
    try:
        check_answers(probabilities, choices, questions)
    except ValueError:
        return None
    initial = adopt_buttons(choices, questions)
    kept = dict(initial)
    remove_opposite_dpad(kept)
    adopted: dict[str, str | None] = {}
    for question in questions:
        value: str | None = None
        if question.kind == "button":
            if question.target in kept:
                value = kept[question.target][0]
            elif question.target in initial:
                value = REJECTED
        elif question.kind == "stick_direction":
            direction = choose_direction(question, choices[question.name])
            value = None if direction == NONE_CHOICE else direction
        elif question.kind == "advisor":
            value = REQUESTED if probabilities[question.name] > threshold else None
        adopted[question.name] = value
    return adopted


def adopted_count(adopted: Mapping[str, str | None] | None) -> int | None:
    """操作に採った数を返す。adopted は adopted_answers の結果。

    adopted が None なら None。値が None でも "rejected" でもない問いの数 (押したボタン (押下と短押し)、
    倒したスティックの軸、求めた advisor の役割の数の合計) を返す。
    """
    if adopted is None:
        return None
    return sum(1 for value in adopted.values() if value not in (None, REJECTED))


def request_adopted(
    questions: list[Question], request: dict, threshold: float
) -> dict[str, str | None] | None:
    """要求の行 request の adopted (adopted_answers の結果) を返す。

    request の probabilities と choices がどちらも object (辞書) でなければ (応答が無い要求) None を返す。
    それ以外は adopted_answers(questions, probabilities, choices, threshold) を返す。object でない方は
    空の辞書として渡す (答えが欠けるため、その種類の問いがあれば None になる)。
    """
    probabilities = request.get("probabilities")
    choices = request.get("choices")
    has_probabilities = isinstance(probabilities, Mapping)
    has_choices = isinstance(choices, Mapping)
    if not has_probabilities and not has_choices:
        return None
    return adopted_answers(
        questions,
        probabilities if has_probabilities else {},
        choices if has_choices else {},
        threshold,
    )
