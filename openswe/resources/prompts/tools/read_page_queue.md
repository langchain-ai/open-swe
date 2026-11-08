Read how far the person's dashboard page has carried out the commands you queued with its page tools.

Returns `queued_through`, the position of the last command you queued; `executed_through`, the last position the page carried out along with every one before it; and `results`, what each of your commands after `since` returned, in order (at most 20 at a time).
Pass `since` as the last position whose result you have already read.
Pass `wait_for` as a command's position to wait, up to 20 seconds, until the page has carried it out; pass the position a listing tool returned to read what it found in the same turn.
The page carries out commands within moments while the person has it open; past the wait, a command it has not reached stays queued until they come back to it.
