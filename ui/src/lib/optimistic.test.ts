import { QueryClient } from "@tanstack/react-query"
import { expect, it } from "vitest"

import { optimisticUpdate } from "./optimistic"

type Row = { id: string; on: boolean }
const key = ["rows"]
const toggle = (id: string, on: boolean) => (rows: Array<Row>) =>
  rows.map((row) => (row.id === id ? { ...row, on } : row))
const states = (client: QueryClient) =>
  client.getQueryData<Array<Row>>(key)?.map((row) => row.on)

function seeded() {
  const client = new QueryClient()
  client.setQueryData<Array<Row>>(key, [
    { id: "a", on: false },
    { id: "b", on: false },
  ])
  return client
}

it("undoes its own update when nothing else has written since", async () => {
  const client = seeded()
  const undo = await optimisticUpdate(client, key, toggle("a", true))
  expect(states(client)).toEqual([true, false])

  undo()

  expect(states(client)).toEqual([false, false])
})

it("keeps a later row's update when an earlier one fails", async () => {
  const client = seeded()
  const undoA = await optimisticUpdate(client, key, toggle("a", true))
  await optimisticUpdate(client, key, toggle("b", true))

  undoA()

  expect(states(client)).toEqual([true, true])
  expect(client.getQueryState(key)?.isInvalidated).toBe(true)
})
