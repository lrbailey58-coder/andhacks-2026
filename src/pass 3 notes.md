We've finally moved on to integrating the backend api, here are visual bugs.

The failed "Interpreting rules" status appears duplicated and pinned to the bottom of the feed. 
As more statuses were added to the feed the failed "Interpreting rules" status
remained at the bottom of the feed. I suspect this
happened after the questions.

The player swarm simulation step has a few issues.
 - The ellipses appears between the status message and the checkbox, it would look
 better if the ellipses were to the right of the checkbox.
 - The simulation does not have a hard time limit behind the scenes, just a turn
 limit, we should be able to configure the time limit for player simulation from
 a constant in the backend/ folder.
 - Finally, the user should have a manual override button inside the player swarm status (also to the LEFT of the ellipses) to prematurely conclude the player simulation ASAP
 - "Show Sample" checkbox does not show the sample game from the swarm as it is
 playing, only after it is done playing. 
 - The displayed sample is empty is empty.

Likewise, the design review and code blocks appear blank.

The design review status appears with a checkmark before is complete.

My recommendation is to simplify some of the logic in the UI and actually remove
some of the unit tests concerning the layout of the website on the understanding
that the CSS and general architecture are more or less stable, since this will
make it easier to spot messier problems like bad logic with how status messages
are handled.