"""advisor の強制の呼び出し。Decision Model の advisor の問いが真にならなくても、決まった条件で advisor を呼ぶ。

速い層が行き詰まりに気づかないと、advisor の問いの確率は低いままになり、advisor が何分も呼ばれないことがある。
強制の呼び出しの方式は base.AdvisorTrigger の型で差し替えられる。今ある方式は idle_timer.IdleTimerTrigger
(advisor が決まった秒数呼ばれていなければ呼ぶ) だけ。
"""
