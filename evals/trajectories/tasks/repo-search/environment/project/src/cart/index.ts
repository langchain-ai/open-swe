import { resolveCheckoutRoute } from "../routing/checkout"

export const checkoutLink = (cartId: string) => resolveCheckoutRoute(cartId)
