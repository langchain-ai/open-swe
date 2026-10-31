import { useDashboardTheme, useHostContext } from "@openswe/plugin-sdk/react"
import { Button } from "@langchain/macaw-components/Button"
import { Card } from "@langchain/macaw-components/Card"
import { Text } from "@langchain/macaw-components/Text"
import { useState } from "react"

/** Replace this with the app. Macaw's components follow the web UI's light or dark theme. */
export function App() {
  useDashboardTheme()
  const { user } = useHostContext()
  const [count, setCount] = useState(0)
  return (
    <main className="flex h-full items-center justify-center p-space-6">
      <Card className="flex w-full max-w-sm flex-col gap-space-4 p-space-6">
        <Text as="h1" variant="h5" weight="medium">
          Hello, {user.name ?? user.login}
        </Text>
        <Text color="secondary">Clicked {count} times.</Text>
        <Button onClick={() => setCount((n) => n + 1)}>Click</Button>
      </Card>
    </main>
  )
}
