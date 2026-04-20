import { configureStore } from '@reduxjs/toolkit'
import { useDispatch, useSelector } from 'react-redux'
import researchReducer from './researchSlice'
import papersReducer from './papersSlice'
import settingsReducer from './settingsSlice'
import chatReducer from './chatSlice'

export const store = configureStore({
  reducer: {
    research: researchReducer,
    papers: papersReducer,
    settings: settingsReducer,
    chat: chatReducer,
  },
})

export type RootState = ReturnType<typeof store.getState>
export type AppDispatch = typeof store.dispatch

export const useAppDispatch = useDispatch.withTypes<AppDispatch>()
export const useAppSelector = useSelector.withTypes<RootState>()
