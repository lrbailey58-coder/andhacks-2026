# Strengths
## Wide application space
Any system that can be described in a turn based fashion does work. Not to mention the act of describing a desired system in this way is often very revealing for the user. 

## Clarifying questions are really good
The rules agent is great at picking out ambiguities from the input. Maybe some experimentation is in order with removing the intermediate formalization step to see how important it is to the process. 

## Code is a good artifact
The rules agent also writes code which the user can take home and use. This code follows a regular interface in a way which is softly enforced by the backend api. Could be upgraded to strong enforcement using a compiled programming language. Strong typing could also add a ton of guarantees about the immutability of individual board states.

## Compelling user loop
The core interaction loop is easy to understand and people we presented it to wanted to engage with it. This is probably more important than anything.

# Weaknesses
## Player agent behavior
Player agents often behave the same way. Players can be assigned strategic personalities to make them vary from each other. This is especially important in games that involve "social thinking" as opposed to raw analytics. 

We also intend to have Player agents playing against tree search algorithms in the future. such algorithms need to have Frameworks to build their own game state evaluations. mcts is promising, though the current system of agents playing against each other may be the most in line with the project philosophy.

Players also need more instructions on how to use the game interface provided to them by the rules agent. This could involve tools to look ahead some max number of turns or call specialized constant functions on the game state.

## Front end architecture
The motivation of using flask was to avoid requiring a REST api or similar with the python backend. for a demo, this also made it easier to keep everything on one machine. However, the way the rules agent implemented games ended up putting a lot of json and parsing logic into the system anyway, the same complexity thay would have come from using a REST api with a plain javascript website, and the added complexity of flask was detrimental to the iteration speed of the front end design.

## Lack of sandboxing
Game code execution needs a better sandboxing system to scale well to more complicated game. Especially if the user specifically requests architectural aspects about the code like touching files or network details. 

## Artefacts could be presented better to the user.
Even on small projects, the code, Player runs, and design reviews quickly become hard to manage. We need to decide what this would look like as a full system: accounts, monetization, and the system which these two would support, robust project management. Additionally, the "chatbot" style design needs to be revised to minimize the amount of things the user has to do to get to their objects for a given project.

