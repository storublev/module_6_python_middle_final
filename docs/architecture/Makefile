# Пересобирает картинки схем из исходников PlantUML.
#
# PlantUML запускается в контейнере: ставить Java и graphviz на машину ради
# трёх картинок незачем, а в образе они уже есть. На macOS docker может быть
# не в PATH — тогда:
#   export PATH="$PATH:/Applications/Docker.app/Contents/Resources/bin"
#
#   make          — собрать изменившиеся схемы
#   make clean    — удалить картинки

# Версия закреплена: с плавающим тегом latest схемы однажды перестанут
# собираться из-за несовместимости, и это вскроется в самый неподходящий момент.
IMAGE ?= plantuml/plantuml:1.2026.8
SOURCES := $(wildcard *.puml)
IMAGES := $(SOURCES:.puml=.png)

.PHONY: all clean
all: $(IMAGES)

%.png: %.puml
	docker run --rm --user "$(shell id -u):$(shell id -g)" \
		-v "$(CURDIR)":/work -w /work $(IMAGE) -tpng -charset UTF-8 $<

clean:
	rm -f $(IMAGES)
