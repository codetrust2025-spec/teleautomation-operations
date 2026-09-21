// ESLint for `npm run lint:recruitment`, the only thing that runs ESLint here.
//
// ESLint 9 refuses to run without a flat config, and this repository never had
// one, so the script failed before reading a line. This is the smallest config
// that lets it lint the recruitment panels meaningfully: parse modern JSX for
// the browser and catch the mistakes that break a component at runtime -- a
// name that is not defined, a hook called conditionally, an import left behind.
//
// It is deliberately not a style guide. The plugins' recommended sets would
// also flag every component prop for missing PropTypes and hundreds of lines no
// change has touched; add a rule here only when it catches real breakage.
import globals from 'globals'
import react from 'eslint-plugin-react'
import reactHooks from 'eslint-plugin-react-hooks'

export default [
  { ignores: ['dist/**', 'node_modules/**'] },
  {
    files: ['src/**/*.{js,jsx}'],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: { ...globals.browser },
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    plugins: { react, 'react-hooks': reactHooks },
    settings: { react: { version: 'detect' } },
    rules: {
      'no-undef': 'error',
      'no-unused-vars': ['warn', { args: 'none', ignoreRestSiblings: true }],
      // Count <Component /> and JSX itself as uses, so an import that JSX
      // needs is not reported as unused.
      'react/jsx-uses-react': 'error',
      'react/jsx-uses-vars': 'error',
      'react/jsx-no-undef': 'error',
      'react-hooks/rules-of-hooks': 'error',
    },
  },
]
