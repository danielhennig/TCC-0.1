# Atalhos para quem tem make (Linux/macOS). A lógica fica em tasks.py.
TASKS := setup reset up down smoke bench simulate retime features evaluate robustness report fit score build dashboard dashboard-stop monitor monitor-fit test lint fmt lock psql shell
.PHONY: $(TASKS)
$(TASKS):
	python tasks.py $@
