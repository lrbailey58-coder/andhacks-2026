This is the user interface description for a tool to assist in the design of
turn-based games.

# This Document
This document outlines the form and function of the front end. The back end is
roughly an agent swarm with codegen, game simulation, etc. The behavior 
specified here is what information to display to the user and how, and when and
how to ask for input.

The goal is to allow the user to specify a game, narrow down ambiguities on the
rules with questions, then automatically implement and test the game, then 
recieve feedback on their designs, at which point they can append more
information to their game definition.

This document also describes a minimum viable product. The minimum viable 
product will not have production features such as accounts, project management,
long term memories, etc.

# Components
 - <Title>: A title containing "Let's design <Multiword>". When the user submits
 a prompt, the title collapses to the left.
 - <PromptEditor>: A text editor with the default text "Describe a board game,
 scenario or simulation, alternatively, drag and drop a file" and a <ShowCode> to the right.
 - <ShowCode>: A checkbox labeled "show me the code"
 - <Multiword>: A word that periodically scrolls down and fades out to reveal
 another word. In <Title>: "Let's design _an Experience_" "Let's design _a Scenario_" etc.
 - <Ellipses>: A "..." where each . periodically bounces.
 - <Result>: A checkmark or cross indicating the success or failure of a task.
 - <Status>: A non-interactive status message containing text and ending in an
 <Ellipses>.
 - <StatusInterpreting>: A <Status> that says "Interpreting rules".
 - <StatusCoding>: A <Status> that says "Writing game code".
 - <StatusDeploying>: A <Status> that says "Deploying game instances".
 - <StatusPlaying>: A <Status> that says "Herding player agent swarm".
 - <ShowSample>: A checkbox "Show sample game" that shows a sample of the player agent swarm reasoning through the game with the generated rules.
 - <StatusCollecting>: A <Status> that says "Collecting gameplay data and analyses".
 - <StatusQuestion>: A <Status> containing a question from the backend about how the
 game rules work.
 - <LongBlock>: Content that types itself out over time, typing faster over time
 to not hang on long blocks. Has copy and download buttons.
 - <LongBlockResponse>: A <LongBlock> of text provided by the Design agent (see Logic),
 finally giving feedback on the player's input.
 - <LongBlockCode>: A <LongBlock> of code provided by the Rules agent (see Logic),
 displaying the code generated to simulate games.
 - <Ping>: An invisible element which plays a sound. Used to alert the user that
 a task has completed.

# Style
Style should take after existing chatbots while taking themes from combinatorial
games. For example, <StatusPlaying> can have a checkerboard emoji while 
appearing in a thin grey font in the same way that status messages do in 
commercial ChatGPT and Gemini.

Components generally appear in a vertical feed. Layouts will be discussed more
thoroughly in Logic. 

Instead of specifying specific component styles, here is a list of DOs and DONTs.

DOs
 - Grid patterns and small assorted arrows.
 - Curved lines indicate changes in direction, like <StatusQuestion> and <Response>
 - For fonts, readability accepts no compromises. All text needs to be
 highlightable.
 - Components used in different contexts should appear subtly differently. This
 is because they usually do different things.
 - Smooth, simple motion.
 - After writing CSS, ask yourself if there are any large horizontal gaps
 at any point in the vertical flow, or any vertical gaps between entries in the
 flow, that could be caused and could need patching up.

DONTs
 - Gradients and fullscreen animations
 - Exotic flow that users will not recognize from working with chatbots or
 coding agents.
 - Thin or ostentatious fonts.

# Logic
This section is the formal description, from the end-user perspective, of the
flow of execution of the software.

## General Rules
All components named in Components are presented in a vertical feed unless
otherwise specified. The vertical feed begins centered vertically and
horizontally.

When a <Status> is shown, all <Ellipses> components from previous statuses are
replaced by <Result> with a default value of "success", displaying as a checkmark.

<Result> generally do not appear with a value of "failure" unless explicitly
told so by the API. This is a thing that can happen though and should be tested
for, as the backend is agent-based and may suffer loss of service or other
issues outside of our control.

When the user pressed enter while typing in a <PromptEditor>, they "submit" the
prompt, an action that is referred to later. Alternatively, if the 
user presses enter with a modifier, any modifier, it's treated like a regular 
enter keypress inside the editor. Once submitted, the <PromptEditor> slides from
covering the vertical feed horizontally to a right alignment and gains a copy
and download button at the bottom.

<Status> components are left aligned in the feed.

<LongBlock> components cover the feed horizontally.

The feed takes up most of the screen horizontally, but not all, and obviously
scrolls horizontally when its contents surpass its vertical size.

## Main Flow
The user is presented with a <Title> followed by a <PromptEditor>

The user writes their prompt or drags and drops a file into the <PromptEditor>,
then submits it.

Once the prompt is submitted, the <Title> is hidden, a <StatusInterpreting> is shown. At this point, a
<StatusQuestion> or <StatusCoding> may appear.

<StatusQuestion> shows another <PromptEditor> and waits for the user to submit before another <StatusInterpreting> 
appears, creating a loop.

When a <StatusQuestion> appears, it's because the actual
"status" being referred to by the previous <StatusInterpreting> "failed"- the
Rules Agent doesn't fully understand the user's input and needs clarification
to continue.
So the <Result> placed in that previous status will be a failure in this case.

After <StatusCoding> appears, the interpreting step is finished, and the rest
of the flow is linear from the user's perspective. 

<StatusDeploying> appears eventually. If <ShowCode>
was checked earlier and the process, since the coding is complete, the code can
now be retrieved from the back end and displayed. The API's confirmation of
completion of the coding step should have come with information on the code's
contents, which we can store and use here.

<StatusPlaying> appears eventually alongside an unchecked <ShowSample>. If <ShowSample>

<StatusCollecting> appears eventually.

Eventually, a <LongBlockResponse> appears with both <ShowCode> and <ShowSample>
checkboxes at the bottom. Checking each shows the generated code and completed
sample game from the previous run. If both are checked, the code and sample game
appear in adjacent columns, similar to a online code debugger.

Finally, a simple left-aligned text box is shown saying "What's next?", followed by a <PromptEditor>

# Dummy API
To test the components, provide a dummy API that will be merged with the real
API later. This app will be built on Flask with the backend being an assortment
of Python scripts connecting to the Gemini API. What that means for us is no
fancy REST API, json parsing, or database management, the "API", in it's 
production form, will just be a bunch of exposed python functions being called
by flask, so the Dummy should match that shape.

The Dummy API should match expected outputs for a few scenarios.

# Testing
The UI should survive at least two tests:

A test where the user specifies a game and asks for everything.

A test where the user specifies a game poorly and must answer a question to
continue.

Tests can be written using pytest.
