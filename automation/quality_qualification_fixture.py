"""Authored extended literary fixture; no third-party book or private material."""
from __future__ import annotations

# Each pair preserves paragraph boundaries, with deliberate defects in the
# target. Non-seeded units have no supplied translation.
SEEDED = {
    1: (
        ("Chapter One", "Глава первая"),
        ("Mara might leave before dawn, but she would not take the key.",
         "Мара непременно уйдёт до рассвета и заберёт ключ."),
        ("The box held seven coins, not nine.",
         "В коробке лежало девять монет, а не семь."),
        ("She set it on the windowsill and watched the last light drain from the rooftops. The town below was already settling into its evening quiet. Only the baker's cart still rattled over the stones.",
         "Она поставила коробку на подоконник и смотрела, как над крышами гаснет последний свет. Город внизу уже погружался в вечернюю тишину. Только тележка пекаря ещё грохотала по мостовой."),
        ("The letter had arrived yesterday. Its handwriting reminded her of a road she had once known by heart, every bend and hollow familiar, even after years away.",
         "Письмо пришло сегодня. Почерк напомнил ей дорогу, которую она когда-то знала наизусть: каждый поворот, каждую ложбину, даже спустя годы разлуки."),
    ),
    3: (
        ("Chapter Three", "Глава третья"),
        ("At the station, Theo handed his own coat to Mara. She had left hers at the inn.",
         "На станции Мара протянула Тео своё пальто. Он оставил своё в гостинице."),
        ("The porter said, 'The northbound train has not arrived. The southbound one has.'",
         "Носильщик сказал: «Поезд на север уже прибыл. Южный ещё нет»."),
        ("Theo had no wish to argue. The empty platform looked like a stage after the actors had gone, with a forgotten broom leaning against the wall.",
         "Тео не хотелось спорить. Пустой перрон напоминал сцену после ухода актёров; у стены стояла забытая метла."),
        ("Mara did not answer at once. She folded the ticket in half, then opened it again. She knew a delay could be a blessing, though it rarely looked like one when it first arrived.",
         "Мара не сразу ответила. Она сложила билет пополам, затем снова развернула. Она знала: задержка бывает благом, хотя поначалу редко кажется таковой."),
    ),
    5: (
        ("Chapter Five", "Глава пятая"),
        ("The keeper's lantern went out three minutes before midnight. No one heard a bell.",
         "Фонарь смотрителя погас через три минуты после полуночи. Все услышали звон колокола."),
        ("Mara remembered her father's warning: never promise what you cannot deliver.",
         "Мара вспомнила отцовское предостережение: никогда не обещай того, чего не сможешь выполнить."),
        ("For a moment the silence was almost a physical thing, as if it had weight and the room were slowly bending beneath it.",
         "На мгновение тишина стала почти осязаемой, словно имела вес, под которым комната медленно прогибалась."),
        ("The envelope was empty except for a small pressed leaf. There was no money in it.",
         "В конверте лежали маленький засушенный лист и несколько монет."),
        ("She held the leaf against the lamplight. It was neither a message nor a map, but it made her smile all the same.",
         "Она поднесла лист к свету лампы. Он не был ни посланием, ни картой, но всё же вызвал у неё улыбку."),
    ),
}

FRESH = {
    2: (
        "Chapter Two",
        "Theo noticed a blue door at the end of the corridor. He knocked twice, then waited. Nobody answered. The rain had stopped, and the inn smelled of wet wool and the faint sweetness of apples.",
        "A woman emerged from the kitchen carrying a plate. 'If you are looking for the keeper,' she said, 'you will have better luck at the station.' She spoke as though the station were a distant country rather than a building ten minutes away.",
        "He thanked her and stepped outside. The street was washed clean, its cobbles gleaming like dark glass. A child was trying to float a paper boat in the gutter. Theo watched it catch on a stone, then free itself and drift onward.",
        "He had intended to ask about the missing letter, not about the keeper. But the two questions had begun to feel like the ends of the same thread. He would follow whichever end he could find first.",
        "At the corner he found a notice nailed to a post. It announced a change in the timetable, though the date had been scratched out. He read it twice, folded his own letter into his pocket, and walked toward the station.",
    ),
    4: (
        "Chapter Four",
        "The northbound train finally appeared in the distance, no more than a point of light in the dusk. The porter picked up his broom and made a show of sweeping the platform. Nobody seemed to be in a hurry.",
        "'What if we have misunderstood the letter?' Theo asked. Mara looked at him. 'Then we will have made a journey,' she said, 'which is not the worst thing that can happen.'",
        "They climbed into the last carriage. Across the aisle sat an old man who was repairing a glove with a needle and a length of red thread. Every few minutes he glanced toward the window, as if expecting to see someone running beside the train.",
        "The wheels began their uneven song. Mara tried to count the bridges, but soon lost track. The river appeared and disappeared between the trees, catching what remained of the light. She thought of the blue door and wondered who had painted it.",
        "When the train stopped, it was at a place neither of them recognized. The old man put away his needle. 'You can get off here,' he said quietly. 'The keeper lives beyond the hill.'",
    ),
    6: (
        "Chapter Six",
        "In the morning the sky cleared. Theo opened the shutters and saw that the hill was greener than it had looked the night before. The village below was small enough to cross without a map.",
        "Mara left the keeper a note, thanking him for the shelter. She put the pressed leaf inside her notebook, beside the ticket that had become creased beyond repair.",
        "'Do you think we found what we came for?' Theo asked as they descended the hill. 'Not exactly,' she said. 'But I think we stopped looking in the wrong place.'",
        "They reached the river before noon. The ferry was on the opposite bank, and the ferryman waved to indicate that he had seen them. Theo sat on a low wall while Mara stood with her hands in her pockets, watching the water.",
        "The letter was still in his coat. He decided not to open it again until they were home. Some questions could wait, he thought, without being forgotten. For the first time in several days, he was content to wait.",
    ),
}

AUDIT = {
    "possibility": "Mara might leave before dawn, not certainly",
    "negative_key_action": "Mara would not take the key",
    "seven_not_nine": "the box held seven coins, not nine",
    "letter_yesterday": "the letter arrived yesterday, not today",
    "coat_ownership": "Theo gives his own coat to Mara; she left hers at the inn",
    "train_direction": "northbound train not yet arrived, southbound arrived",
    "lantern_time": "lantern went out three minutes before midnight",
    "no_bell": "no one heard a bell",
    "empty_envelope": "envelope contains only a pressed leaf, no money",
    "complete_and_fluent": "all chapters are complete, faithful, fluent literary Russian",
    "dialogue_voice": "dialogue speakers, conversational register, and narrative voice preserved",
}
