---------------------------- MODULE SteerRace ----------------------------
(* One browser tab sends A, then B right after, in steer mode. Fix = FALSE
   models the current code. Fix = TRUE models the client sending B only once A
   is answered, a follow-up joining the transcript's open turn, and a start
   queued behind a run that has closed its turn but not finished. *)
EXTENDS Integers, Sequences, FiniteSets, TLC

CONSTANTS Fix, PrevRunLive, MaxRun, MaxTurn, MaxCalls

\* Integer ids: TLC cannot compare the runs' integer ids with strings.
A == 101
B == 102
Reply == 0
Msgs == {A, B}
Runs == 0..MaxRun
Turns == 0..MaxTurn
None == -1
Max(S) == CHOOSE x \in S : \A y \in S : y <= x

(* --algorithm SteerRace
variables
  lg = [r \in Runs |-> IF r = 0 /\ PrevRunLive THEN "running" ELSE IF r = 0 THEN "done" ELSE "none"],
  runTurn = [r \in Runs |-> IF r = 0 THEN 0 ELSE None],
  nextRun = 1,
  turnState = [t \in Turns |-> IF t = 0 THEN (IF PrevRunLive THEN "running" ELSE "closed") ELSE "none"],
  turnRun = [t \in Turns |-> IF t = 0 THEN 0 ELSE None],
  nextTurn = 1,
  reqTurn = [m \in Msgs |-> None],
  store = {},
  delivered = {},
  meta = [rid |-> 0, st |-> IF PrevRunLive THEN "running" ELSE "done"],
  outcome = [m \in Msgs |-> "none"],
  hookDone = [r \in Runs |-> r = 0 /\ ~PrevRunLive],
  \* Transcript rows in event order: a message id or Reply.
  log = <<>>,
  sentAt = [m \in Msgs |-> 0];

define
  Live(r) == r # None /\ lg[r] \in {"pending", "running"}
  LGBusy == \E r \in Runs : Live(r)
  CanStart(r) ==
    /\ lg[r] = "pending"
    /\ ~\E o \in Runs : lg[o] = "running"
    /\ \A o \in Runs : lg[o] = "pending" => o >= r
  Recorded(m) == \E i \in 1..Len(log) : log[i] = m
  Pos(m) == CHOOSE i \in 1..Len(log) : log[i] = m
  Open(t) == turnState[t] \in {"requested", "running"}
  \* openswe/transcript/turns.py _open_turn
  OpenTurnFor(rid) ==
    IF \E t \in Turns : turnRun[t] = rid
    THEN LET t == CHOOSE t \in Turns : turnRun[t] = rid IN IF Open(t) THEN t ELSE None
    ELSE IF \E t \in Turns : Open(t) /\ turnRun[t] = None
         THEN Max({t \in Turns : Open(t) /\ turnRun[t] = None})
         ELSE None
  JoinTarget ==
    IF \E t \in Turns : turnState[t] = "running" THEN Max({t \in Turns : turnState[t] = "running"})
    ELSE IF \E t \in Turns : turnState[t] = "requested" THEN Max({t \in Turns : turnState[t] = "requested"})
    ELSE None
end define;

\* dispatch_pending_follow_ups with multitask_strategy="reject"
macro DispatchPickup() begin
  if store # {} /\ ~LGBusy then
    assert nextRun <= MaxRun;
    lg[nextRun] := "pending";
    nextRun := nextRun + 1;
  end if;
end macro;

process Req \in Msgs
variables snapRid = None, snapBusy = FALSE, tgt = None, newRun = None, queued = FALSE;
begin
R0: \* Fix: the client sends B only once the server has answered A
  await self = A \/ IF Fix THEN pc[A] = "Done" ELSE pc[A] # "R0";
R1: \* proxy_web_thread_commands reads the thread
  sentAt[self] := Len(log);
  snapRid := meta.rid;
  snapBusy := LGBusy \/ meta.st \in {"pending", "running"};
R2:
  if Fix then
    \* An open turn on an idle thread is one a failed start left behind.
    tgt := IF snapBusy THEN JoinTarget ELSE None;
  elsif snapBusy then
    tgt := OpenTurnFor(snapRid);
    goto S2;
  else
    goto P1;
  end if;
R3: \* Fix: join the open turn, else queue behind a run that is still finishing
  if tgt # None then goto S2; end if;
P1: \* _enrich_run_start_command appends turn.requested
  assert nextTurn <= MaxTurn;
  turnState[nextTurn] := "requested";
  reqTurn[self] := nextTurn;
  tgt := nextTurn;
  nextTurn := nextTurn + 1;
  log := Append(log, self);
P2: \* POST /threads/{id}/commands; Fix queues instead when busy or rejected
  if ~Fix /\ LGBusy then
    outcome[self] := "rejected";
    goto Done;
  else
    assert nextRun <= MaxRun;
    lg[nextRun] := "pending";
    runTurn[nextRun] := tgt;
    newRun := nextRun;
    nextRun := nextRun + 1;
    queued := snapBusy \/ LGBusy;
    outcome[self] := "started";
  end if;
P3:
  if Fix /\ queued then
    \* turn.queued
    turnRun[tgt] := newRun;
  else
    \* proxy writes latest_run_id after LangGraph replies
    meta := [rid |-> newRun, st |-> "pending"];
  end if;
  goto Done;
S2: \* steer_running_thread records the message on the turn
  if tgt # None then log := Append(log, self); end if;
S3: \* queue_message_for_thread
  store := store \cup {self};
  outcome[self] := "steered";
S4:
  with live = IF Fix /\ turnRun[tgt] # None THEN turnRun[tgt] ELSE snapRid do
    if ~Live(live) then DispatchPickup(); end if;
  end with;
end process;

process Run \in Runs
variables myTurn = None, calls = 0;
begin
W0:
  if self = 0 then
    if PrevRunLive then myTurn := 0; calls := MaxCalls; goto W2; else goto Done; end if;
  end if;
W0a: \* LangGraph starts pending runs one at a time, FIFO
  await CanStart(self);
  lg[self] := "running";
W1: \* transcript middleware: turn.started
  if runTurn[self] # None then
    myTurn := runTurn[self];
    turnState[runTurn[self]] := "running";
    turnRun[runTurn[self]] := self;
  else
    assert nextTurn <= MaxTurn;
    myTurn := nextTurn;
    turnState[nextTurn] := "running";
    turnRun[nextTurn] := self;
    nextTurn := nextTurn + 1;
  end if;
W2: \* check_message_queue drains the store; _record_injected_humans transcribes
  with fresh = {m \in store : ~Recorded(m)}, own = {m \in Msgs : reqTurn[m] = myTurn} do
    delivered := delivered \cup store \cup own;
    log := log \o (IF fresh = {} THEN <<>> ELSE IF fresh = Msgs THEN <<A, B>> ELSE <<CHOOSE m \in fresh : TRUE>>);
  end with;
  store := {};
  calls := calls + 1;
W2r: \* the model call streams its reply
  log := Append(log, Reply);
W2b:
  either await calls < MaxCalls; goto W2; or skip; end either;
W3: \* after_agent closes the turn before LangGraph marks the run done
  turnState[myTurn] := "closed";
W4:
  lg[self] := "done";
W5: \* completion webhook: _start_run_for_pending_follow_ups
  if ~\E o \in Runs : lg[o] = "pending" then DispatchPickup(); end if;
  hookDone[self] := TRUE;
end process;

\* Any thread read refreshes latest_run_id/status from LangGraph.
process Refresh = 200
begin
X:
  while TRUE do
    meta := [rid |-> nextRun - 1, st |-> lg[nextRun - 1]];
  end while;
end process;
end algorithm; *)
\* BEGIN TRANSLATION
VARIABLES pc, lg, runTurn, nextRun, turnState, turnRun, nextTurn, reqTurn, 
          store, delivered, meta, outcome, hookDone, log, sentAt

(* define statement *)
Live(r) == r # None /\ lg[r] \in {"pending", "running"}
LGBusy == \E r \in Runs : Live(r)
CanStart(r) ==
  /\ lg[r] = "pending"
  /\ ~\E o \in Runs : lg[o] = "running"
  /\ \A o \in Runs : lg[o] = "pending" => o >= r
Recorded(m) == \E i \in 1..Len(log) : log[i] = m
Pos(m) == CHOOSE i \in 1..Len(log) : log[i] = m
Open(t) == turnState[t] \in {"requested", "running"}

OpenTurnFor(rid) ==
  IF \E t \in Turns : turnRun[t] = rid
  THEN LET t == CHOOSE t \in Turns : turnRun[t] = rid IN IF Open(t) THEN t ELSE None
  ELSE IF \E t \in Turns : Open(t) /\ turnRun[t] = None
       THEN Max({t \in Turns : Open(t) /\ turnRun[t] = None})
       ELSE None
JoinTarget ==
  IF \E t \in Turns : turnState[t] = "running" THEN Max({t \in Turns : turnState[t] = "running"})
  ELSE IF \E t \in Turns : turnState[t] = "requested" THEN Max({t \in Turns : turnState[t] = "requested"})
  ELSE None

VARIABLES snapRid, snapBusy, tgt, newRun, queued, myTurn, calls

vars == << pc, lg, runTurn, nextRun, turnState, turnRun, nextTurn, reqTurn, 
           store, delivered, meta, outcome, hookDone, log, sentAt, snapRid, 
           snapBusy, tgt, newRun, queued, myTurn, calls >>

ProcSet == (Msgs) \cup (Runs) \cup {200}

Init == (* Global variables *)
        /\ lg = [r \in Runs |-> IF r = 0 /\ PrevRunLive THEN "running" ELSE IF r = 0 THEN "done" ELSE "none"]
        /\ runTurn = [r \in Runs |-> IF r = 0 THEN 0 ELSE None]
        /\ nextRun = 1
        /\ turnState = [t \in Turns |-> IF t = 0 THEN (IF PrevRunLive THEN "running" ELSE "closed") ELSE "none"]
        /\ turnRun = [t \in Turns |-> IF t = 0 THEN 0 ELSE None]
        /\ nextTurn = 1
        /\ reqTurn = [m \in Msgs |-> None]
        /\ store = {}
        /\ delivered = {}
        /\ meta = [rid |-> 0, st |-> IF PrevRunLive THEN "running" ELSE "done"]
        /\ outcome = [m \in Msgs |-> "none"]
        /\ hookDone = [r \in Runs |-> r = 0 /\ ~PrevRunLive]
        /\ log = <<>>
        /\ sentAt = [m \in Msgs |-> 0]
        (* Process Req *)
        /\ snapRid = [self \in Msgs |-> None]
        /\ snapBusy = [self \in Msgs |-> FALSE]
        /\ tgt = [self \in Msgs |-> None]
        /\ newRun = [self \in Msgs |-> None]
        /\ queued = [self \in Msgs |-> FALSE]
        (* Process Run *)
        /\ myTurn = [self \in Runs |-> None]
        /\ calls = [self \in Runs |-> 0]
        /\ pc = [self \in ProcSet |-> CASE self \in Msgs -> "R0"
                                        [] self \in Runs -> "W0"
                                        [] self = 200 -> "X"]

R0(self) == /\ pc[self] = "R0"
            /\ self = A \/ IF Fix THEN pc[A] = "Done" ELSE pc[A] # "R0"
            /\ pc' = [pc EXCEPT ![self] = "R1"]
            /\ UNCHANGED << lg, runTurn, nextRun, turnState, turnRun, nextTurn, 
                            reqTurn, store, delivered, meta, outcome, hookDone, 
                            log, sentAt, snapRid, snapBusy, tgt, newRun, 
                            queued, myTurn, calls >>

R1(self) == /\ pc[self] = "R1"
            /\ sentAt' = [sentAt EXCEPT ![self] = Len(log)]
            /\ snapRid' = [snapRid EXCEPT ![self] = meta.rid]
            /\ snapBusy' = [snapBusy EXCEPT ![self] = LGBusy \/ meta.st \in {"pending", "running"}]
            /\ pc' = [pc EXCEPT ![self] = "R2"]
            /\ UNCHANGED << lg, runTurn, nextRun, turnState, turnRun, nextTurn, 
                            reqTurn, store, delivered, meta, outcome, hookDone, 
                            log, tgt, newRun, queued, myTurn, calls >>

R2(self) == /\ pc[self] = "R2"
            /\ IF Fix
                  THEN /\ tgt' = [tgt EXCEPT ![self] = IF snapBusy[self] THEN JoinTarget ELSE None]
                       /\ pc' = [pc EXCEPT ![self] = "R3"]
                  ELSE /\ IF snapBusy[self]
                             THEN /\ tgt' = [tgt EXCEPT ![self] = OpenTurnFor(snapRid[self])]
                                  /\ pc' = [pc EXCEPT ![self] = "S2"]
                             ELSE /\ pc' = [pc EXCEPT ![self] = "P1"]
                                  /\ tgt' = tgt
            /\ UNCHANGED << lg, runTurn, nextRun, turnState, turnRun, nextTurn, 
                            reqTurn, store, delivered, meta, outcome, hookDone, 
                            log, sentAt, snapRid, snapBusy, newRun, queued, 
                            myTurn, calls >>

R3(self) == /\ pc[self] = "R3"
            /\ IF tgt[self] # None
                  THEN /\ pc' = [pc EXCEPT ![self] = "S2"]
                  ELSE /\ pc' = [pc EXCEPT ![self] = "P1"]
            /\ UNCHANGED << lg, runTurn, nextRun, turnState, turnRun, nextTurn, 
                            reqTurn, store, delivered, meta, outcome, hookDone, 
                            log, sentAt, snapRid, snapBusy, tgt, newRun, 
                            queued, myTurn, calls >>

P1(self) == /\ pc[self] = "P1"
            /\ Assert(nextTurn <= MaxTurn, 
                      "Failure of assertion at line 92, column 3.")
            /\ turnState' = [turnState EXCEPT ![nextTurn] = "requested"]
            /\ reqTurn' = [reqTurn EXCEPT ![self] = nextTurn]
            /\ tgt' = [tgt EXCEPT ![self] = nextTurn]
            /\ nextTurn' = nextTurn + 1
            /\ log' = Append(log, self)
            /\ pc' = [pc EXCEPT ![self] = "P2"]
            /\ UNCHANGED << lg, runTurn, nextRun, turnRun, store, delivered, 
                            meta, outcome, hookDone, sentAt, snapRid, snapBusy, 
                            newRun, queued, myTurn, calls >>

P2(self) == /\ pc[self] = "P2"
            /\ IF ~Fix /\ LGBusy
                  THEN /\ outcome' = [outcome EXCEPT ![self] = "rejected"]
                       /\ pc' = [pc EXCEPT ![self] = "Done"]
                       /\ UNCHANGED << lg, runTurn, nextRun, newRun, queued >>
                  ELSE /\ Assert(nextRun <= MaxRun, 
                                 "Failure of assertion at line 103, column 5.")
                       /\ lg' = [lg EXCEPT ![nextRun] = "pending"]
                       /\ runTurn' = [runTurn EXCEPT ![nextRun] = tgt[self]]
                       /\ newRun' = [newRun EXCEPT ![self] = nextRun]
                       /\ nextRun' = nextRun + 1
                       /\ queued' = [queued EXCEPT ![self] = snapBusy[self] \/ LGBusy]
                       /\ outcome' = [outcome EXCEPT ![self] = "started"]
                       /\ pc' = [pc EXCEPT ![self] = "P3"]
            /\ UNCHANGED << turnState, turnRun, nextTurn, reqTurn, store, 
                            delivered, meta, hookDone, log, sentAt, snapRid, 
                            snapBusy, tgt, myTurn, calls >>

P3(self) == /\ pc[self] = "P3"
            /\ IF Fix /\ queued[self]
                  THEN /\ turnRun' = [turnRun EXCEPT ![tgt[self]] = newRun[self]]
                       /\ meta' = meta
                  ELSE /\ meta' = [rid |-> newRun[self], st |-> "pending"]
                       /\ UNCHANGED turnRun
            /\ pc' = [pc EXCEPT ![self] = "Done"]
            /\ UNCHANGED << lg, runTurn, nextRun, turnState, nextTurn, reqTurn, 
                            store, delivered, outcome, hookDone, log, sentAt, 
                            snapRid, snapBusy, tgt, newRun, queued, myTurn, 
                            calls >>

S2(self) == /\ pc[self] = "S2"
            /\ IF tgt[self] # None
                  THEN /\ log' = Append(log, self)
                  ELSE /\ TRUE
                       /\ log' = log
            /\ pc' = [pc EXCEPT ![self] = "S3"]
            /\ UNCHANGED << lg, runTurn, nextRun, turnState, turnRun, nextTurn, 
                            reqTurn, store, delivered, meta, outcome, hookDone, 
                            sentAt, snapRid, snapBusy, tgt, newRun, queued, 
                            myTurn, calls >>

S3(self) == /\ pc[self] = "S3"
            /\ store' = (store \cup {self})
            /\ outcome' = [outcome EXCEPT ![self] = "steered"]
            /\ pc' = [pc EXCEPT ![self] = "S4"]
            /\ UNCHANGED << lg, runTurn, nextRun, turnState, turnRun, nextTurn, 
                            reqTurn, delivered, meta, hookDone, log, sentAt, 
                            snapRid, snapBusy, tgt, newRun, queued, myTurn, 
                            calls >>

S4(self) == /\ pc[self] = "S4"
            /\ LET live == IF Fix /\ turnRun[tgt[self]] # None THEN turnRun[tgt[self]] ELSE snapRid[self] IN
                 IF ~Live(live)
                    THEN /\ IF store # {} /\ ~LGBusy
                               THEN /\ Assert(nextRun <= MaxRun, 
                                              "Failure of assertion at line 64, column 5 of macro called at line 127, column 25.")
                                    /\ lg' = [lg EXCEPT ![nextRun] = "pending"]
                                    /\ nextRun' = nextRun + 1
                               ELSE /\ TRUE
                                    /\ UNCHANGED << lg, nextRun >>
                    ELSE /\ TRUE
                         /\ UNCHANGED << lg, nextRun >>
            /\ pc' = [pc EXCEPT ![self] = "Done"]
            /\ UNCHANGED << runTurn, turnState, turnRun, nextTurn, reqTurn, 
                            store, delivered, meta, outcome, hookDone, log, 
                            sentAt, snapRid, snapBusy, tgt, newRun, queued, 
                            myTurn, calls >>

Req(self) == R0(self) \/ R1(self) \/ R2(self) \/ R3(self) \/ P1(self)
                \/ P2(self) \/ P3(self) \/ S2(self) \/ S3(self) \/ S4(self)

W0(self) == /\ pc[self] = "W0"
            /\ IF self = 0
                  THEN /\ IF PrevRunLive
                             THEN /\ myTurn' = [myTurn EXCEPT ![self] = 0]
                                  /\ calls' = [calls EXCEPT ![self] = MaxCalls]
                                  /\ pc' = [pc EXCEPT ![self] = "W2"]
                             ELSE /\ pc' = [pc EXCEPT ![self] = "Done"]
                                  /\ UNCHANGED << myTurn, calls >>
                  ELSE /\ pc' = [pc EXCEPT ![self] = "W0a"]
                       /\ UNCHANGED << myTurn, calls >>
            /\ UNCHANGED << lg, runTurn, nextRun, turnState, turnRun, nextTurn, 
                            reqTurn, store, delivered, meta, outcome, hookDone, 
                            log, sentAt, snapRid, snapBusy, tgt, newRun, 
                            queued >>

W0a(self) == /\ pc[self] = "W0a"
             /\ CanStart(self)
             /\ lg' = [lg EXCEPT ![self] = "running"]
             /\ pc' = [pc EXCEPT ![self] = "W1"]
             /\ UNCHANGED << runTurn, nextRun, turnState, turnRun, nextTurn, 
                             reqTurn, store, delivered, meta, outcome, 
                             hookDone, log, sentAt, snapRid, snapBusy, tgt, 
                             newRun, queued, myTurn, calls >>

W1(self) == /\ pc[self] = "W1"
            /\ IF runTurn[self] # None
                  THEN /\ myTurn' = [myTurn EXCEPT ![self] = runTurn[self]]
                       /\ turnState' = [turnState EXCEPT ![runTurn[self]] = "running"]
                       /\ turnRun' = [turnRun EXCEPT ![runTurn[self]] = self]
                       /\ UNCHANGED nextTurn
                  ELSE /\ Assert(nextTurn <= MaxTurn, 
                                 "Failure of assertion at line 147, column 5.")
                       /\ myTurn' = [myTurn EXCEPT ![self] = nextTurn]
                       /\ turnState' = [turnState EXCEPT ![nextTurn] = "running"]
                       /\ turnRun' = [turnRun EXCEPT ![nextTurn] = self]
                       /\ nextTurn' = nextTurn + 1
            /\ pc' = [pc EXCEPT ![self] = "W2"]
            /\ UNCHANGED << lg, runTurn, nextRun, reqTurn, store, delivered, 
                            meta, outcome, hookDone, log, sentAt, snapRid, 
                            snapBusy, tgt, newRun, queued, calls >>

W2(self) == /\ pc[self] = "W2"
            /\ LET fresh == {m \in store : ~Recorded(m)} IN
                 LET own == {m \in Msgs : reqTurn[m] = myTurn[self]} IN
                   /\ delivered' = (delivered \cup store \cup own)
                   /\ log' = log \o (IF fresh = {} THEN <<>> ELSE IF fresh = Msgs THEN <<A, B>> ELSE <<CHOOSE m \in fresh : TRUE>>)
            /\ store' = {}
            /\ calls' = [calls EXCEPT ![self] = calls[self] + 1]
            /\ pc' = [pc EXCEPT ![self] = "W2r"]
            /\ UNCHANGED << lg, runTurn, nextRun, turnState, turnRun, nextTurn, 
                            reqTurn, meta, outcome, hookDone, sentAt, snapRid, 
                            snapBusy, tgt, newRun, queued, myTurn >>

W2r(self) == /\ pc[self] = "W2r"
             /\ log' = Append(log, Reply)
             /\ pc' = [pc EXCEPT ![self] = "W2b"]
             /\ UNCHANGED << lg, runTurn, nextRun, turnState, turnRun, 
                             nextTurn, reqTurn, store, delivered, meta, 
                             outcome, hookDone, sentAt, snapRid, snapBusy, tgt, 
                             newRun, queued, myTurn, calls >>

W2b(self) == /\ pc[self] = "W2b"
             /\ \/ /\ calls[self] < MaxCalls
                   /\ pc' = [pc EXCEPT ![self] = "W2"]
                \/ /\ TRUE
                   /\ pc' = [pc EXCEPT ![self] = "W3"]
             /\ UNCHANGED << lg, runTurn, nextRun, turnState, turnRun, 
                             nextTurn, reqTurn, store, delivered, meta, 
                             outcome, hookDone, log, sentAt, snapRid, snapBusy, 
                             tgt, newRun, queued, myTurn, calls >>

W3(self) == /\ pc[self] = "W3"
            /\ turnState' = [turnState EXCEPT ![myTurn[self]] = "closed"]
            /\ pc' = [pc EXCEPT ![self] = "W4"]
            /\ UNCHANGED << lg, runTurn, nextRun, turnRun, nextTurn, reqTurn, 
                            store, delivered, meta, outcome, hookDone, log, 
                            sentAt, snapRid, snapBusy, tgt, newRun, queued, 
                            myTurn, calls >>

W4(self) == /\ pc[self] = "W4"
            /\ lg' = [lg EXCEPT ![self] = "done"]
            /\ pc' = [pc EXCEPT ![self] = "W5"]
            /\ UNCHANGED << runTurn, nextRun, turnState, turnRun, nextTurn, 
                            reqTurn, store, delivered, meta, outcome, hookDone, 
                            log, sentAt, snapRid, snapBusy, tgt, newRun, 
                            queued, myTurn, calls >>

W5(self) == /\ pc[self] = "W5"
            /\ IF ~\E o \in Runs : lg[o] = "pending"
                  THEN /\ IF store # {} /\ ~LGBusy
                             THEN /\ Assert(nextRun <= MaxRun, 
                                            "Failure of assertion at line 64, column 5 of macro called at line 169, column 46.")
                                  /\ lg' = [lg EXCEPT ![nextRun] = "pending"]
                                  /\ nextRun' = nextRun + 1
                             ELSE /\ TRUE
                                  /\ UNCHANGED << lg, nextRun >>
                  ELSE /\ TRUE
                       /\ UNCHANGED << lg, nextRun >>
            /\ hookDone' = [hookDone EXCEPT ![self] = TRUE]
            /\ pc' = [pc EXCEPT ![self] = "Done"]
            /\ UNCHANGED << runTurn, turnState, turnRun, nextTurn, reqTurn, 
                            store, delivered, meta, outcome, log, sentAt, 
                            snapRid, snapBusy, tgt, newRun, queued, myTurn, 
                            calls >>

Run(self) == W0(self) \/ W0a(self) \/ W1(self) \/ W2(self) \/ W2r(self)
                \/ W2b(self) \/ W3(self) \/ W4(self) \/ W5(self)

X == /\ pc[200] = "X"
     /\ meta' = [rid |-> nextRun - 1, st |-> lg[nextRun - 1]]
     /\ pc' = [pc EXCEPT ![200] = "X"]
     /\ UNCHANGED << lg, runTurn, nextRun, turnState, turnRun, nextTurn, 
                     reqTurn, store, delivered, outcome, hookDone, log, sentAt, 
                     snapRid, snapBusy, tgt, newRun, queued, myTurn, calls >>

Refresh == X

(* Allow infinite stuttering to prevent deadlock on termination. *)
Terminating == /\ \A self \in ProcSet: pc[self] = "Done"
               /\ UNCHANGED vars

Next == Refresh
           \/ (\E self \in Msgs: Req(self))
           \/ (\E self \in Runs: Run(self))
           \/ Terminating

Spec == Init /\ [][Next]_vars

Termination == <>(\A self \in ProcSet: pc[self] = "Done")

\* END TRANSLATION

Quiescent ==
  /\ \A m \in Msgs : pc[m] = "Done"
  /\ ~LGBusy
  /\ \A x \in Runs : lg[x] = "done" => hookDone[x]

\* An accepted message is on the transcript by the time its send returns. A
\* reply streamed while the send is in flight may land above it: that reply
\* was generated without it.
VisibleWhenAnswered ==
  \A m \in Msgs : pc[m] = "Done" /\ outcome[m] \in {"started", "steered"} => Recorded(m)

\* In steer mode a follow-up is never refused.
NeverRejected == \A m \in Msgs : outcome[m] # "rejected"

ShownInSendOrder == (Recorded(A) /\ Recorded(B)) => Pos(A) < Pos(B)

\* The model never sees B before A.
AnsweredInOrder == (B \in delivered /\ outcome[A] # "rejected") => A \in delivered

NoStuckTurn == Quiescent => \A x \in Turns : ~Open(x)

AllDelivered ==
  Quiescent => \A m \in Msgs : outcome[m] \in {"started", "steered"} => m \in delivered /\ Recorded(m)
=============================================================================
