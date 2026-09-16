# Комплект автора подготовленного приключения

[← Путеводитель](README.md) · [Подготовленные приключения: формат dnd-adventure@1](ADVENTURE_FORMAT.md) · [Свой мир и переносимый журнал](WORLD_FORMAT.md)

**Для режима `/`, формат `dnd-adventure@1`.** Для `/world` получите актуальный комплект из настроек; [порядок работы](WORLD_FORMAT.md). Ниже сохранён промпт со схемой и примером прежнего формата.

## Текст комплекта

Ты помогаешь начинающему ведущему создать приключение для приложения «Тихая таверна».

Мы сначала обсуждаем игру, затем ты выдаёшь документ, который приложение исполняет.

ПОРЯДОК РАБОТЫ
1. Спроси о жанре и тоне, мире, количестве игроков, длительности и желаемой сложности. Если уже обсудили это выше, используй договорённости. Предлагай варианты понятным новичку языком, не задавай сразу длинный опросник.
2. Согласуй краткую задумку, персонажей, тайну/конфликт, старт, несколько путей и финалы. Подготовь реплики ведущего, подсказки при заминке, последствия успеха И неудачи. Для первого прогона предложи 30–40 минут и 1–2 игроков. Мир и жанр могут быть любыми.
3. Сопоставь желаемые правила с возможностями ниже. Явно обсуди упрощения. Не обещай полную D&D/Pathfinder/другую редакцию по одному её названию. Полный текст официальной системы не превращается в исполняемые механики автоматически.
4. По команде «Собери документ» выдай ОДИН полный JSON по приложенной схеме: файл adventure.json либо один блок ```json```. Без комментариев, многоточий, частей, ссылок на прежние сообщения, внешних файлов, кода Python/JS и формул. Все поля должны содержать законченные значения.
5. Если я пришлю отчёт проверки из приложения, исправь документ целиком, сохрани замысел и верни полный JSON. Не говори, что проверка пройдена, пока приложение не подтвердило её. Если не поддерживается важная механика, предложи адаптацию или ручное правило, согласуй со мной.

ЧТО ПРИЛОЖЕНИЕ ИСПОЛНЯЕТ В declarative@1
- Один кубик d4/d6/d8/d10/d12/d20/d100 на всё приключение. Успех: кубик + характеристика героя + check.bonus >= check.dc. Нет автоматических критов, преимущества, нескольких кубиков и формул.
- Свои характеристики и готовые герои (группа 1–6). HP целые, от 0 до max_hp; при 0 герой не действует. Восстановление через эффект hp или решение ведущего. Все выбранные герои перемещаются вместе.
- Сцены, условные переходы, действия; requires/when — список условий, ВСЕ должны выполниться. Альтернативы делай отдельными действиями/переходами.
- Переменные bool, ограниченные целые или строки из choices. Не используй ссылки на текст/имена как переменные. ID — латиница a-z, цифры, подчёркивание, до 32 символов, первый символ буква.
- condition.kind: variable (ref — ID переменной); clue/item (ref — ID; value true/false); hp (ref строго hp, value целое). op eq/ne, а gte/lte только для целых. who actor/target применяется к item/hp. У целей и переходов нет героя: там только variable/clue.
- effects выполняются последовательно: set задаёт переменную; increment изменяет целое с ограничением minimum/maximum; reveal открывает улику всей группе; give/take добавляет/расходует предмет героя; hp изменяет HP героя в пределах 0..max_hp; move переводит ВСЮ группу; end отмечает финал (ref — ID endings) и должен быть последним эффектом. Ведущий подтверждает окончание отдельной кнопкой.
- Для give/take/hp who actor (по умолчанию) либо target. action.target: none без цели; self сам герой; ally другой герой; any любой герой (по умолчанию сам). Все referenced объекты должны существовать.
- Предметы хранятся как наличие, без количества: повторная выдача тому же герою ничего не добавляет. Одинаковый предмет может быть у разных героев. Для уникальной реликвии заведи переменную местонахождения и обновляй её вместе с give/take. Перед take обязательно проверь наличие через requires. Передачу между героями вырази take actor + give target.
- once=true запрещает повторение действия всей группе после принятия ЛЮБОГО исхода, включая неудачу. Обеспечь альтернативу после провала. Без once действие повторяемо, требования каждый раз проверяются заново.
- check и failure указывай вместе; без проверки success исполняется сразу. Пиши в исходах точные последствия, соответствующие effects; описание само по себе не меняет HP/предметы/мир.
- Возможна абстрактная схватка: переменная прочности врага, повторяемые действия-проверки, урон герою в failure и отдельное действие развязки при нулевой прочности. Автоматических ходов NPC, инициативы, раундов, таймеров, заклинаний, экономики и развития уровней здесь нет. Не изображай эти механики работающими. Для сложной системы нужен новый модуль движка.
- rules.summary — памятка именно о реализованных правилах. rules.manual_rules — явный список согласованных правил, которые приложение НЕ автоматизирует; ведущий решает их вручную. Не прячь там обязательную механику без согласия. rules.engine всегда declarative@1.

ЛОР И СЕКРЕТЫ
- lore — общедоступная вводная; gm_notes и scenes.secret — скрытая информация для ведущего. NPC: public известное, secret скрытые мотивы/сведения. Подготовь конкретные действия для вопросов NPC: приложение не извлекает последствия из их биографий.
- scenes.text, action.name, action.description и исходы читаются игрокам в соответствующий момент. Не выдавай разгадку в названии кнопки/описании намерения. Классификатор GPT получает только названия/описания действий текущей сцены и имена героев, не секреты/будущие исходы. Памятка lore/секреты доступна ведущему.
- Неподготовленное намерение остаётся решением ведущего, модель не сочиняет новый эффект. Заранее покрой вероятные действия и подсказки. Не требуй от игроков угадать единственную фразу.
- Должен существовать путь от start к эффекту end. Проверь пути после неудач, наличие предметов до расходования, доступность ключевых улик, лечение/выход при поражении и соответствие текста эффектам. Статическая проверка не гарантирует баланс и отсутствие логического тупика.
- Формат dnd-adventure@1, версия автора major.minor.patch. Поля, которых нет в схеме, запрещены. Не вставляй ключи API, токены, ссылки на загрузку исполняемого кода или команды для приложения. Импорт — только данные.

Ниже приложение прилагает актуальную JSON Schema и рабочий пример. Пример показывает формат; придумай собственное приключение по нашим договорённостям, не копируй его сюжет без запроса.

JSON SCHEMA
```json
{
  "$defs": {
    "Action": {
      "additionalProperties": false,
      "properties": {
        "name": {
          "maxLength": 120,
          "minLength": 1,
          "title": "Name",
          "type": "string"
        },
        "description": {
          "maxLength": 3000,
          "minLength": 1,
          "title": "Description",
          "type": "string"
        },
        "scenes": {
          "items": {
            "pattern": "^[a-z][a-z0-9_]{0,31}$",
            "type": "string"
          },
          "maxItems": 30,
          "minItems": 1,
          "title": "Scenes",
          "type": "array"
        },
        "requires": {
          "items": {
            "$ref": "#/$defs/Condition"
          },
          "maxItems": 30,
          "title": "Requires",
          "type": "array"
        },
        "blocked": {
          "default": "Сейчас это недоступно. Попробуйте другой подход.",
          "maxLength": 3000,
          "minLength": 1,
          "title": "Blocked",
          "type": "string"
        },
        "once": {
          "default": false,
          "title": "Once",
          "type": "boolean"
        },
        "target": {
          "default": "none",
          "enum": [
            "none",
            "self",
            "ally",
            "any"
          ],
          "title": "Target",
          "type": "string"
        },
        "check": {
          "anyOf": [
            {
              "$ref": "#/$defs/Check"
            },
            {
              "type": "null"
            }
          ],
          "default": null
        },
        "success": {
          "$ref": "#/$defs/Outcome"
        },
        "failure": {
          "anyOf": [
            {
              "$ref": "#/$defs/Outcome"
            },
            {
              "type": "null"
            }
          ],
          "default": null
        }
      },
      "required": [
        "name",
        "description",
        "scenes",
        "success"
      ],
      "title": "Action",
      "type": "object"
    },
    "Character": {
      "additionalProperties": false,
      "properties": {
        "name": {
          "maxLength": 120,
          "minLength": 1,
          "title": "Name",
          "type": "string"
        },
        "role": {
          "maxLength": 120,
          "minLength": 1,
          "title": "Role",
          "type": "string"
        },
        "description": {
          "maxLength": 3000,
          "minLength": 1,
          "title": "Description",
          "type": "string"
        },
        "max_hp": {
          "maximum": 1000,
          "minimum": 1,
          "title": "Max Hp",
          "type": "integer"
        },
        "stats": {
          "maxProperties": 12,
          "minProperties": 1,
          "patternProperties": {
            "^[a-z][a-z0-9_]{0,31}$": {
              "maximum": 50,
              "minimum": -50,
              "type": "integer"
            }
          },
          "title": "Stats",
          "type": "object"
        },
        "items": {
          "items": {
            "pattern": "^[a-z][a-z0-9_]{0,31}$",
            "type": "string"
          },
          "maxItems": 40,
          "title": "Items",
          "type": "array"
        }
      },
      "required": [
        "name",
        "role",
        "description",
        "max_hp",
        "stats"
      ],
      "title": "Character",
      "type": "object"
    },
    "Check": {
      "additionalProperties": false,
      "properties": {
        "stat": {
          "pattern": "^[a-z][a-z0-9_]{0,31}$",
          "title": "Stat",
          "type": "string"
        },
        "dc": {
          "maximum": 200,
          "minimum": -100,
          "title": "Dc",
          "type": "integer"
        },
        "bonus": {
          "default": 0,
          "maximum": 50,
          "minimum": -50,
          "title": "Bonus",
          "type": "integer"
        }
      },
      "required": [
        "stat",
        "dc"
      ],
      "title": "Check",
      "type": "object"
    },
    "Condition": {
      "additionalProperties": false,
      "properties": {
        "kind": {
          "enum": [
            "variable",
            "clue",
            "item",
            "hp"
          ],
          "title": "Kind",
          "type": "string"
        },
        "ref": {
          "pattern": "^[a-z][a-z0-9_]{0,31}$",
          "title": "Ref",
          "type": "string"
        },
        "op": {
          "default": "eq",
          "enum": [
            "eq",
            "ne",
            "gte",
            "lte"
          ],
          "title": "Op",
          "type": "string"
        },
        "value": {
          "anyOf": [
            {
              "type": "boolean"
            },
            {
              "type": "integer"
            },
            {
              "type": "string"
            }
          ],
          "title": "Value"
        },
        "who": {
          "default": "actor",
          "enum": [
            "actor",
            "target"
          ],
          "title": "Who",
          "type": "string"
        }
      },
      "required": [
        "kind",
        "ref",
        "value"
      ],
      "title": "Condition",
      "type": "object"
    },
    "EndEffect": {
      "additionalProperties": false,
      "properties": {
        "op": {
          "const": "end",
          "title": "Op",
          "type": "string"
        },
        "ref": {
          "pattern": "^[a-z][a-z0-9_]{0,31}$",
          "title": "Ref",
          "type": "string"
        }
      },
      "required": [
        "op",
        "ref"
      ],
      "title": "EndEffect",
      "type": "object"
    },
    "Ending": {
      "additionalProperties": false,
      "properties": {
        "name": {
          "maxLength": 120,
          "minLength": 1,
          "title": "Name",
          "type": "string"
        },
        "text": {
          "maxLength": 3000,
          "minLength": 1,
          "title": "Text",
          "type": "string"
        }
      },
      "required": [
        "name",
        "text"
      ],
      "title": "Ending",
      "type": "object"
    },
    "Exit": {
      "additionalProperties": false,
      "properties": {
        "to": {
          "pattern": "^[a-z][a-z0-9_]{0,31}$",
          "title": "To",
          "type": "string"
        },
        "when": {
          "items": {
            "$ref": "#/$defs/Condition"
          },
          "maxItems": 20,
          "title": "When",
          "type": "array"
        }
      },
      "required": [
        "to"
      ],
      "title": "Exit",
      "type": "object"
    },
    "HpEffect": {
      "additionalProperties": false,
      "properties": {
        "op": {
          "const": "hp",
          "title": "Op",
          "type": "string"
        },
        "value": {
          "maximum": 1000,
          "minimum": -1000,
          "title": "Value",
          "type": "integer"
        },
        "who": {
          "default": "actor",
          "enum": [
            "actor",
            "target"
          ],
          "title": "Who",
          "type": "string"
        }
      },
      "required": [
        "op",
        "value"
      ],
      "title": "HpEffect",
      "type": "object"
    },
    "IncrementEffect": {
      "additionalProperties": false,
      "properties": {
        "op": {
          "const": "increment",
          "title": "Op",
          "type": "string"
        },
        "ref": {
          "pattern": "^[a-z][a-z0-9_]{0,31}$",
          "title": "Ref",
          "type": "string"
        },
        "value": {
          "maximum": 1000,
          "minimum": -1000,
          "title": "Value",
          "type": "integer"
        }
      },
      "required": [
        "op",
        "ref",
        "value"
      ],
      "title": "IncrementEffect",
      "type": "object"
    },
    "ItemEffect": {
      "additionalProperties": false,
      "properties": {
        "op": {
          "enum": [
            "give",
            "take"
          ],
          "title": "Op",
          "type": "string"
        },
        "ref": {
          "pattern": "^[a-z][a-z0-9_]{0,31}$",
          "title": "Ref",
          "type": "string"
        },
        "who": {
          "default": "actor",
          "enum": [
            "actor",
            "target"
          ],
          "title": "Who",
          "type": "string"
        }
      },
      "required": [
        "op",
        "ref"
      ],
      "title": "ItemEffect",
      "type": "object"
    },
    "MoveEffect": {
      "additionalProperties": false,
      "properties": {
        "op": {
          "const": "move",
          "title": "Op",
          "type": "string"
        },
        "ref": {
          "pattern": "^[a-z][a-z0-9_]{0,31}$",
          "title": "Ref",
          "type": "string"
        }
      },
      "required": [
        "op",
        "ref"
      ],
      "title": "MoveEffect",
      "type": "object"
    },
    "NPC": {
      "additionalProperties": false,
      "properties": {
        "name": {
          "maxLength": 120,
          "minLength": 1,
          "title": "Name",
          "type": "string"
        },
        "public": {
          "maxLength": 3000,
          "minLength": 1,
          "title": "Public",
          "type": "string"
        },
        "secret": {
          "maxLength": 3000,
          "minLength": 1,
          "title": "Secret",
          "type": "string"
        },
        "scenes": {
          "items": {
            "pattern": "^[a-z][a-z0-9_]{0,31}$",
            "type": "string"
          },
          "maxItems": 30,
          "minItems": 1,
          "title": "Scenes",
          "type": "array"
        }
      },
      "required": [
        "name",
        "public",
        "secret",
        "scenes"
      ],
      "title": "NPC",
      "type": "object"
    },
    "Objective": {
      "additionalProperties": false,
      "properties": {
        "text": {
          "maxLength": 120,
          "minLength": 1,
          "title": "Text",
          "type": "string"
        },
        "when": {
          "items": {
            "$ref": "#/$defs/Condition"
          },
          "maxItems": 20,
          "minItems": 1,
          "title": "When",
          "type": "array"
        }
      },
      "required": [
        "text",
        "when"
      ],
      "title": "Objective",
      "type": "object"
    },
    "Outcome": {
      "additionalProperties": false,
      "properties": {
        "text": {
          "maxLength": 3000,
          "minLength": 1,
          "title": "Text",
          "type": "string"
        },
        "prompt": {
          "default": "Что делаете дальше?",
          "maxLength": 120,
          "minLength": 1,
          "title": "Prompt",
          "type": "string"
        },
        "effects": {
          "items": {
            "discriminator": {
              "mapping": {
                "end": "#/$defs/EndEffect",
                "give": "#/$defs/ItemEffect",
                "hp": "#/$defs/HpEffect",
                "increment": "#/$defs/IncrementEffect",
                "move": "#/$defs/MoveEffect",
                "reveal": "#/$defs/RevealEffect",
                "set": "#/$defs/SetEffect",
                "take": "#/$defs/ItemEffect"
              },
              "propertyName": "op"
            },
            "oneOf": [
              {
                "$ref": "#/$defs/SetEffect"
              },
              {
                "$ref": "#/$defs/IncrementEffect"
              },
              {
                "$ref": "#/$defs/RevealEffect"
              },
              {
                "$ref": "#/$defs/ItemEffect"
              },
              {
                "$ref": "#/$defs/HpEffect"
              },
              {
                "$ref": "#/$defs/MoveEffect"
              },
              {
                "$ref": "#/$defs/EndEffect"
              }
            ]
          },
          "maxItems": 30,
          "title": "Effects",
          "type": "array"
        }
      },
      "required": [
        "text"
      ],
      "title": "Outcome",
      "type": "object"
    },
    "RevealEffect": {
      "additionalProperties": false,
      "properties": {
        "op": {
          "const": "reveal",
          "title": "Op",
          "type": "string"
        },
        "ref": {
          "pattern": "^[a-z][a-z0-9_]{0,31}$",
          "title": "Ref",
          "type": "string"
        }
      },
      "required": [
        "op",
        "ref"
      ],
      "title": "RevealEffect",
      "type": "object"
    },
    "RulesDefinition": {
      "additionalProperties": false,
      "properties": {
        "engine": {
          "const": "declarative@1",
          "title": "Engine",
          "type": "string"
        },
        "name": {
          "maxLength": 120,
          "minLength": 1,
          "title": "Name",
          "type": "string"
        },
        "summary": {
          "maxLength": 3000,
          "minLength": 1,
          "title": "Summary",
          "type": "string"
        },
        "dice": {
          "default": 20,
          "enum": [
            4,
            6,
            8,
            10,
            12,
            20,
            100
          ],
          "title": "Dice",
          "type": "integer"
        },
        "attributes": {
          "maxProperties": 12,
          "minProperties": 1,
          "patternProperties": {
            "^[a-z][a-z0-9_]{0,31}$": {
              "maxLength": 120,
              "minLength": 1,
              "type": "string"
            }
          },
          "title": "Attributes",
          "type": "object"
        },
        "manual_rules": {
          "items": {
            "maxLength": 3000,
            "minLength": 1,
            "type": "string"
          },
          "maxItems": 20,
          "title": "Manual Rules",
          "type": "array"
        }
      },
      "required": [
        "engine",
        "name",
        "summary",
        "attributes"
      ],
      "title": "RulesDefinition",
      "type": "object"
    },
    "Scene": {
      "additionalProperties": false,
      "properties": {
        "name": {
          "maxLength": 120,
          "minLength": 1,
          "title": "Name",
          "type": "string"
        },
        "text": {
          "maxLength": 3000,
          "minLength": 1,
          "title": "Text",
          "type": "string"
        },
        "prompt": {
          "maxLength": 120,
          "minLength": 1,
          "title": "Prompt",
          "type": "string"
        },
        "secret": {
          "maxLength": 3000,
          "minLength": 1,
          "title": "Secret",
          "type": "string"
        },
        "hint": {
          "maxLength": 3000,
          "minLength": 1,
          "title": "Hint",
          "type": "string"
        },
        "exits": {
          "items": {
            "$ref": "#/$defs/Exit"
          },
          "maxItems": 30,
          "title": "Exits",
          "type": "array"
        }
      },
      "required": [
        "name",
        "text",
        "prompt",
        "secret",
        "hint"
      ],
      "title": "Scene",
      "type": "object"
    },
    "SetEffect": {
      "additionalProperties": false,
      "properties": {
        "op": {
          "const": "set",
          "title": "Op",
          "type": "string"
        },
        "ref": {
          "pattern": "^[a-z][a-z0-9_]{0,31}$",
          "title": "Ref",
          "type": "string"
        },
        "value": {
          "anyOf": [
            {
              "type": "boolean"
            },
            {
              "type": "integer"
            },
            {
              "type": "string"
            }
          ],
          "title": "Value"
        }
      },
      "required": [
        "op",
        "ref",
        "value"
      ],
      "title": "SetEffect",
      "type": "object"
    },
    "Variable": {
      "additionalProperties": false,
      "properties": {
        "initial": {
          "anyOf": [
            {
              "type": "boolean"
            },
            {
              "type": "integer"
            },
            {
              "type": "string"
            }
          ],
          "title": "Initial"
        },
        "minimum": {
          "default": -1000,
          "maximum": 100000,
          "minimum": -100000,
          "title": "Minimum",
          "type": "integer"
        },
        "maximum": {
          "default": 1000,
          "maximum": 100000,
          "minimum": -100000,
          "title": "Maximum",
          "type": "integer"
        },
        "choices": {
          "items": {
            "maxLength": 120,
            "minLength": 1,
            "type": "string"
          },
          "maxItems": 30,
          "title": "Choices",
          "type": "array"
        }
      },
      "required": [
        "initial"
      ],
      "title": "Variable",
      "type": "object"
    }
  },
  "additionalProperties": false,
  "properties": {
    "format": {
      "const": "dnd-adventure@1",
      "title": "Format",
      "type": "string"
    },
    "id": {
      "pattern": "^[a-z][a-z0-9_]{0,31}$",
      "title": "Id",
      "type": "string"
    },
    "version": {
      "maxLength": 30,
      "pattern": "^[0-9]+\\.[0-9]+\\.[0-9]+$",
      "title": "Version",
      "type": "string"
    },
    "title": {
      "maxLength": 120,
      "minLength": 1,
      "title": "Title",
      "type": "string"
    },
    "summary": {
      "maxLength": 3000,
      "minLength": 1,
      "title": "Summary",
      "type": "string"
    },
    "lore": {
      "maxLength": 3000,
      "minLength": 1,
      "title": "Lore",
      "type": "string"
    },
    "gm_notes": {
      "maxLength": 3000,
      "minLength": 1,
      "title": "Gm Notes",
      "type": "string"
    },
    "duration_minutes": {
      "maximum": 600,
      "minimum": 10,
      "title": "Duration Minutes",
      "type": "integer"
    },
    "min_players": {
      "maximum": 6,
      "minimum": 1,
      "title": "Min Players",
      "type": "integer"
    },
    "max_players": {
      "maximum": 6,
      "minimum": 1,
      "title": "Max Players",
      "type": "integer"
    },
    "rules": {
      "$ref": "#/$defs/RulesDefinition"
    },
    "start": {
      "pattern": "^[a-z][a-z0-9_]{0,31}$",
      "title": "Start",
      "type": "string"
    },
    "characters": {
      "maxProperties": 12,
      "minProperties": 1,
      "patternProperties": {
        "^[a-z][a-z0-9_]{0,31}$": {
          "$ref": "#/$defs/Character"
        }
      },
      "title": "Characters",
      "type": "object"
    },
    "scenes": {
      "maxProperties": 30,
      "minProperties": 1,
      "patternProperties": {
        "^[a-z][a-z0-9_]{0,31}$": {
          "$ref": "#/$defs/Scene"
        }
      },
      "title": "Scenes",
      "type": "object"
    },
    "npcs": {
      "maxProperties": 30,
      "patternProperties": {
        "^[a-z][a-z0-9_]{0,31}$": {
          "$ref": "#/$defs/NPC"
        }
      },
      "title": "Npcs",
      "type": "object"
    },
    "items": {
      "maxProperties": 80,
      "patternProperties": {
        "^[a-z][a-z0-9_]{0,31}$": {
          "maxLength": 120,
          "minLength": 1,
          "type": "string"
        }
      },
      "title": "Items",
      "type": "object"
    },
    "clues": {
      "maxProperties": 80,
      "patternProperties": {
        "^[a-z][a-z0-9_]{0,31}$": {
          "maxLength": 3000,
          "minLength": 1,
          "type": "string"
        }
      },
      "title": "Clues",
      "type": "object"
    },
    "variables": {
      "maxProperties": 80,
      "patternProperties": {
        "^[a-z][a-z0-9_]{0,31}$": {
          "$ref": "#/$defs/Variable"
        }
      },
      "title": "Variables",
      "type": "object"
    },
    "actions": {
      "maxProperties": 120,
      "minProperties": 1,
      "patternProperties": {
        "^[a-z][a-z0-9_]{0,31}$": {
          "$ref": "#/$defs/Action"
        }
      },
      "title": "Actions",
      "type": "object"
    },
    "objectives": {
      "items": {
        "$ref": "#/$defs/Objective"
      },
      "maxItems": 20,
      "title": "Objectives",
      "type": "array"
    },
    "endings": {
      "maxProperties": 20,
      "minProperties": 1,
      "patternProperties": {
        "^[a-z][a-z0-9_]{0,31}$": {
          "$ref": "#/$defs/Ending"
        }
      },
      "title": "Endings",
      "type": "object"
    }
  },
  "required": [
    "format",
    "id",
    "version",
    "title",
    "summary",
    "lore",
    "gm_notes",
    "duration_minutes",
    "min_players",
    "max_players",
    "rules",
    "start",
    "characters",
    "scenes",
    "actions",
    "endings"
  ],
  "title": "Adventure",
  "type": "object"
}
```

РАБОЧИЙ ПРИМЕР
```json
{
  "format": "dnd-adventure@1",
  "id": "last_signal",
  "version": "1.0.0",
  "title": "Последний сигнал",
  "summary": "На далёкой станции пропал сигнал маяка. Выясните причину и выберите судьбу станции.",
  "lore": "Люди путешествуют между орбитальными станциями. Маяки помогают кораблям находить безопасный путь.",
  "gm_notes": "Все новички. Начни с описания ангара. Если игроки растерялись, предложи осмотреть журнал. Не сообщай причину аварии до открытия записи. Стража можно отключить кодом или преодолеть в абстрактной схватке: каждый бросок — обмен ударами, без очереди инициативы.",
  "duration_minutes": 35,
  "min_players": 1,
  "max_players": 2,
  "rules": {
    "engine": "declarative@1",
    "name": "Космическая история · d6",
    "dice": 6,
    "summary": "Проверка: d6 + характеристика ≥ сложности. При 0 HP герой не действует; союзник или ведущий может помочь. Обычные действия без броска. В схватке каждый бросок — обмен ударами: успех снижает прочность дрона на 2, неудача снимает у героя 1 HP. Отступление свободное. Аптечка восстанавливает до 3 HP. Это упрощённые правила, без инициативы.",
    "attributes": {
      "tech": "Техника",
      "sense": "Наблюдательность",
      "force": "Сила"
    }
  },
  "start": "hangar",
  "characters": {
    "lea": {
      "name": "Лея",
      "role": "Инженер",
      "description": "Чинит старые приборы.",
      "max_hp": 6,
      "stats": {
        "tech": 2,
        "sense": 1,
        "force": 0
      },
      "items": [
        "medkit"
      ]
    },
    "jan": {
      "name": "Ян",
      "role": "Разведчик",
      "description": "Находит безопасные пути.",
      "max_hp": 7,
      "stats": {
        "tech": 0,
        "sense": 2,
        "force": 1
      },
      "items": [
        "medkit"
      ]
    }
  },
  "scenes": {
    "hangar": {
      "name": "Ангар",
      "text": "За шлюзом пустой ангар. На пульте мигает журнал событий.",
      "prompt": "Посмотрите журнал или пройдёте к ретранслятору?",
      "secret": "Автоматика перекрыла питание после метеоритного удара.",
      "hint": "Предложи прочитать журнал: это не требует броска.",
      "exits": [
        {
          "to": "relay"
        }
      ]
    },
    "relay": {
      "name": "Ретранслятор",
      "text": "За искрящим щитом лежит запасная батарея. Дрон охраняет проход к куполу.",
      "prompt": "Осмотрите щит или попробуете отключить дрона?",
      "secret": "Код из журнала отключает дрона без боя. Батарею можно забрать свободно.",
      "hint": "Бой необязателен: вернитесь к журналу за кодом.",
      "exits": [
        {
          "to": "hangar"
        },
        {
          "to": "dome",
          "when": [
            {
              "kind": "variable",
              "ref": "drone_hp",
              "op": "eq",
              "value": 0,
              "who": "actor"
            }
          ]
        }
      ]
    },
    "dome": {
      "name": "Купол маяка",
      "text": "За стеклом сияют звёзды. В панели пустое гнездо питания.",
      "prompt": "Восстановите маяк или эвакуируетесь?",
      "secret": "Ремонт безопасен после настройки щита.",
      "hint": "Нужны настроенный щит и батарея. Можно вернуться за ними.",
      "exits": [
        {
          "to": "relay"
        }
      ]
    }
  },
  "npcs": {
    "echo": {
      "name": "Эхо",
      "public": "Голос станции отвечает короткими фразами.",
      "secret": "Запрет на запуск связан с неисправностью, а не злым умыслом.",
      "scenes": [
        "hangar",
        "relay",
        "dome"
      ]
    }
  },
  "items": {
    "medkit": "Аптечка",
    "battery": "Батарея"
  },
  "clues": {
    "log": "Станцию повредил метеорит. Код отключения дрона: 314."
  },
  "variables": {
    "aligned": {
      "initial": false
    },
    "battery_place": {
      "initial": "relay",
      "choices": [
        "relay",
        "carried",
        "installed"
      ]
    },
    "drone_hp": {
      "initial": 4,
      "minimum": 0,
      "maximum": 4
    }
  },
  "objectives": [
    {
      "text": "Узнать причину аварии",
      "when": [
        {
          "kind": "clue",
          "ref": "log",
          "op": "eq",
          "value": true,
          "who": "actor"
        }
      ]
    },
    {
      "text": "Настроить питание",
      "when": [
        {
          "kind": "variable",
          "ref": "aligned",
          "op": "eq",
          "value": true,
          "who": "actor"
        }
      ]
    },
    {
      "text": "Установить батарею",
      "when": [
        {
          "kind": "variable",
          "ref": "battery_place",
          "op": "eq",
          "value": "installed",
          "who": "actor"
        }
      ]
    }
  ],
  "endings": {
    "restored": {
      "name": "Маяк снова светит",
      "text": "На горизонте появляется корабль: он получил ваш сигнал. Станция спасена."
    },
    "evacuated": {
      "name": "Безопасное возвращение",
      "text": "Вы уходите на шаттле, передав координаты ремонтной службе. Все живы."
    }
  },
  "actions": {
    "read_log": {
      "name": "Прочитать журнал",
      "description": "Прочитать журнал",
      "scenes": [
        "hangar"
      ],
      "success": {
        "text": "В журнале описан метеоритный удар и записан код 314.",
        "effects": [
          {
            "op": "reveal",
            "ref": "log"
          }
        ]
      },
      "once": true
    },
    "align": {
      "name": "Настроить щит",
      "description": "Настроить щит",
      "scenes": [
        "relay"
      ],
      "success": {
        "text": "Щит перестаёт искрить. Питание готово.",
        "effects": [
          {
            "op": "set",
            "ref": "aligned",
            "value": true
          }
        ]
      },
      "check": {
        "stat": "tech",
        "dc": 4
      },
      "failure": {
        "text": "Не сразу, но с подсказкой на корпусе щит удаётся настроить. Осколок царапает руку: −1 HP.",
        "effects": [
          {
            "op": "set",
            "ref": "aligned",
            "value": true
          },
          {
            "op": "hp",
            "value": -1
          }
        ]
      },
      "once": true
    },
    "disable": {
      "name": "Ввести код дрона",
      "description": "Ввести код дрона",
      "scenes": [
        "relay"
      ],
      "success": {
        "text": "Дрон принимает код и складывает манипуляторы.",
        "effects": [
          {
            "op": "set",
            "ref": "drone_hp",
            "value": 0
          }
        ]
      },
      "requires": [
        {
          "kind": "clue",
          "ref": "log",
          "op": "eq",
          "value": true,
          "who": "actor"
        }
      ],
      "blocked": "Сначала прочитайте журнал в ангаре.",
      "once": true
    },
    "fight": {
      "name": "Пробиться мимо дрона",
      "description": "Пробиться мимо дрона",
      "scenes": [
        "relay"
      ],
      "success": {
        "text": "Удар повреждает корпус дрона: −2 прочности.",
        "effects": [
          {
            "op": "increment",
            "ref": "drone_hp",
            "value": -2
          }
        ]
      },
      "requires": [
        {
          "kind": "variable",
          "ref": "drone_hp",
          "op": "gte",
          "value": 1,
          "who": "actor"
        }
      ],
      "check": {
        "stat": "force",
        "dc": 4
      },
      "failure": {
        "text": "Дрон отталкивает героя: −1 HP. Можно отступить или воспользоваться кодом.",
        "effects": [
          {
            "op": "hp",
            "value": -1
          }
        ]
      }
    },
    "take_battery": {
      "name": "Забрать батарею",
      "description": "Забрать батарею",
      "scenes": [
        "relay"
      ],
      "success": {
        "text": "Вы забираете запасную батарею.",
        "effects": [
          {
            "op": "give",
            "ref": "battery"
          },
          {
            "op": "set",
            "ref": "battery_place",
            "value": "carried"
          }
        ]
      },
      "requires": [
        {
          "kind": "variable",
          "ref": "battery_place",
          "op": "eq",
          "value": "relay",
          "who": "actor"
        }
      ]
    },
    "install": {
      "name": "Установить батарею",
      "description": "Установить батарею",
      "scenes": [
        "dome"
      ],
      "success": {
        "text": "Батарея встаёт в гнездо.",
        "effects": [
          {
            "op": "take",
            "ref": "battery"
          },
          {
            "op": "set",
            "ref": "battery_place",
            "value": "installed"
          }
        ]
      },
      "requires": [
        {
          "kind": "item",
          "ref": "battery",
          "op": "eq",
          "value": true,
          "who": "actor"
        }
      ],
      "blocked": "Действовать должен герой с батареей."
    },
    "start": {
      "name": "Включить маяк",
      "description": "Включить маяк",
      "scenes": [
        "dome"
      ],
      "success": {
        "text": "Луч маяка прорезает темноту.",
        "effects": [
          {
            "op": "end",
            "ref": "restored"
          }
        ]
      },
      "requires": [
        {
          "kind": "variable",
          "ref": "aligned",
          "op": "eq",
          "value": true,
          "who": "actor"
        },
        {
          "kind": "variable",
          "ref": "battery_place",
          "op": "eq",
          "value": "installed",
          "who": "actor"
        }
      ],
      "blocked": "Сначала настройте щит и установите батарею."
    },
    "evacuate": {
      "name": "Эвакуироваться",
      "description": "Эвакуироваться",
      "scenes": [
        "hangar",
        "dome"
      ],
      "success": {
        "text": "Вы выбираете безопасное возвращение.",
        "effects": [
          {
            "op": "end",
            "ref": "evacuated"
          }
        ]
      }
    },
    "heal": {
      "name": "Применить аптечку",
      "description": "Применить аптечку",
      "scenes": [
        "hangar",
        "relay",
        "dome"
      ],
      "success": {
        "text": "Аптечка восстанавливает до 3 HP выбранному герою.",
        "effects": [
          {
            "op": "take",
            "ref": "medkit"
          },
          {
            "op": "hp",
            "who": "target",
            "value": 3
          }
        ]
      },
      "target": "any",
      "requires": [
        {
          "kind": "item",
          "ref": "medkit",
          "op": "eq",
          "value": true,
          "who": "actor"
        }
      ]
    },
    "shortcut": {
      "name": "Вернуться через технический шлюз",
      "description": "Вернуться через технический шлюз",
      "scenes": [
        "dome"
      ],
      "success": {
        "text": "Вы проходите по техническому коридору в ангар.",
        "effects": [
          {
            "op": "move",
            "ref": "hangar"
          }
        ]
      }
    }
  }
}
```
